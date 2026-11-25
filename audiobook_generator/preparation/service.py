from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from audiobook_generator.preparation.pdf_to_epub import convert_pdf, inspect_pdf
from audiobook_generator.preparation.translate_epub import inspect_epub, translate_epub
from audiobook_generator.queue.naming import safe_book_name


class PreparationService:
    """One persistent preparation worker, independent of the browser session."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database = self.data_dir / "preparation.sqlite3"
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._api_key = os.environ.get("OPENAI_API_KEY", "")
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS preparations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    result_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0,
                    estimated_usd REAL NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT ''
                )
            """)
            db.execute("UPDATE preparations SET status='paused', error='Préparation interrompue. Cliquez sur Réessayer.' WHERE status='running'")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def set_api_key(self, key: str):
        if (key or "").strip():
            self._api_key = key.strip()

    def inspect(self, source: str | Path, kind: str) -> dict:
        if kind == "pdf_to_epub":
            return inspect_pdf(source)
        if kind == "translate_en_fr":
            return inspect_epub(source)
        raise ValueError("Choisissez PDF vers EPUB ou anglais vers français.")

    def add(self, source: str | Path, kind: str, destination: str | Path, confirmed: bool = False, api_key: str = "") -> dict:
        source = Path(source)
        info = self.inspect(source, kind)
        if kind == "translate_en_fr":
            self.set_api_key(api_key)
            if not self._api_key:
                raise ValueError("Ajoutez votre clé API OpenAI pour traduire ce livre.")
            if not confirmed:
                raise ValueError("Analysez le livre et confirmez le coût estimé avant de lancer la traduction.")
        if not str(destination).strip():
            raise ValueError("Indiquez un dossier de destination pour l’EPUB.")
        destination_root = Path(destination).expanduser().resolve()
        if Path("/.dockerenv").exists():
            mounted_root = Path(os.environ.get("AUDIOBOOK_OUTPUT_DIR", "/exports")).resolve()
            if not destination_root.is_relative_to(mounted_root):
                raise ValueError(
                    f"Dans Docker, choisissez un sous-dossier de {mounted_root}. "
                    "Pour changer le dossier sur votre ordinateur, modifiez AUDIOBOOK_EXPORT_DIR dans .env puis redémarrez."
                )
        destination_root.mkdir(parents=True, exist_ok=True)
        job_id = str(uuid.uuid4())
        title = info["title"].strip() or source.stem
        book_name = safe_book_name(title)
        folder = destination_root / f"{book_name}-{job_id[:8]}"
        folder.mkdir()
        result = folder / (f"{book_name} - français.epub" if kind == "translate_en_fr" else f"{book_name}.epub")
        private_dir = self.data_dir / "preparations" / job_id
        private_dir.mkdir(parents=True)
        private_source = private_dir / ("source.pdf" if kind == "pdf_to_epub" else "source.epub")
        try:
            shutil.copy2(source, private_source)
            row = {
                "id": job_id, "kind": kind, "source_path": str(private_source),
                "original_name": source.name, "title": title,
                "destination": str(destination_root), "result_path": str(result),
                "status": "pending", "progress": 0.0,
                "estimated_usd": info.get("estimated_usd", 0.0), "error": "",
            }
            with self._lock, self._connect() as db:
                db.execute("""INSERT INTO preparations
                    (id, kind, source_path, original_name, title, destination,
                     result_path, status, progress, estimated_usd, error)
                    VALUES (:id, :kind, :source_path, :original_name, :title,
                     :destination, :result_path, :status, :progress,
                     :estimated_usd, :error)""", row)
            self.start()
            return row
        except Exception:
            shutil.rmtree(private_dir, ignore_errors=True)
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def list_jobs(self) -> list[dict]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT * FROM preparations ORDER BY sequence DESC").fetchall()
        return [dict(row) for row in rows]

    def _update(self, job_id: str, **changes):
        if not changes:
            return
        allowed = {"status", "progress", "error"}
        if set(changes) - allowed:
            raise ValueError("Champ de préparation inconnu.")
        fields = ", ".join(f"{key}=:{key}" for key in changes)
        with self._lock, self._connect() as db:
            db.execute(f"UPDATE preparations SET {fields} WHERE id=:id", {"id": job_id, **changes})

    def retry(self, job_id: str, api_key: str = ""):
        self.set_api_key(api_key)
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM preparations WHERE id=?", (job_id,)).fetchone()
            if not row or row["status"] not in {"failed", "paused"}:
                raise ValueError("Cette préparation ne peut pas être relancée.")
            db.execute("UPDATE preparations SET status='pending', error='' WHERE id=?", (job_id,))
        self.start()

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._wake.set()
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="epub-preparation")
            self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            if self.run_next():
                continue
            self._wake.wait(1)
            self._wake.clear()

    def run_next(self) -> bool:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM preparations WHERE status='pending' ORDER BY sequence LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE preparations SET status='running' WHERE id=?", (row["id"],))
        if not row:
            return False
        job = dict(row)

        def progress(done, total):
            self._update(job["id"], progress=min(0.99, done / max(total, 1)))

        try:
            if job["kind"] == "pdf_to_epub":
                convert_pdf(job["source_path"], job["result_path"], progress)
            else:
                if not self._api_key:
                    raise ValueError("Clé API manquante. Renseignez-la puis cliquez sur Réessayer.")
                translate_epub(
                    job["source_path"], job["result_path"], api_key=self._api_key,
                    progress=progress,
                    checkpoint_dir=Path(job["source_path"]).parent / "translated",
                )
            shutil.copy2(
                job["result_path"],
                Path(job["source_path"]).parent / Path(job["result_path"]).name,
            )
            self._update(job["id"], status="completed", progress=1.0, error="")
        except Exception as exc:  # noqa: BLE001 - failures become durable job errors
            self._update(job["id"], status="failed", error=str(exc))
        return True

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=30)
