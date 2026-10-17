from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from audiobook_generator.core.m4b_packager import ChapterTrack, M4BPackager
from audiobook_generator.queue.models import BookJob, JobStatus
from audiobook_generator.queue.naming import safe_book_name
from audiobook_generator.queue.store import InvalidTransition, JobStore


class QueueRunner:
    def __init__(
        self,
        store: JobStore,
        process_factory=subprocess.Popen,
        packager=None,
        environ=None,
        poll_interval: float = 0.5,
    ):
        self.store = store
        self.process_factory = process_factory
        self.packager = packager or M4BPackager()
        self.environ = dict(os.environ if environ is None else environ)
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread = None
        self._process = None
        self._current_job_id = None
        self._pause_requested = set()
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._wake_event.set()
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, name="audiobook-queue", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            if not self.run_next():
                self._wake_event.wait(1)
                self._wake_event.clear()

    def _validate_credentials(self, job: BookJob) -> None:
        if job.provider == "openai" and not self.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("La variable OPENAI_API_KEY est absente.")
        if job.provider == "azure" and not (
            self.environ.get("MS_TTS_KEY") and self.environ.get("MS_TTS_REGION")
        ):
            raise RuntimeError("Les variables MS_TTS_KEY et MS_TTS_REGION sont absentes.")

    def _command(self, job: BookJob) -> list[str]:
        main_path = Path(__file__).resolve().parents[2] / "main.py"
        command = [
            sys.executable, str(main_path), job.source_path, job.output_dir,
            "--tts", job.provider, "--voice_name", job.voice,
            "--output_format", "mp3", "--worker_count", "1", "--no_prompt",
        ]
        if job.model:
            command.extend(["--model_name", job.model])
        if job.provider == "openai" and job.model == "gpt-4o-mini-tts":
            command.extend([
                "--language", "fr-FR", "--instructions",
                "Voix chaleureuse, posée et humaine. Articulation claire, rythme naturel, "
                "pauses souples entre les paragraphes, sans ton publicitaire.",
            ])
        return command

    @staticmethod
    def _tracks(job: BookJob) -> list[ChapterTrack]:
        paths = sorted(Path(job.output_dir).glob("[0-9][0-9][0-9][0-9]_*.mp3"))
        return [
            ChapterTrack(path, path.stem.split("_", 1)[-1].replace("_", " "))
            for path in paths if path.stat().st_size > 0
        ]

    def _update_progress(self, job: BookJob) -> None:
        count = len(self._tracks(job))
        total = max(1, job.analysis.chapter_count)
        self.store.update(
            job.id,
            completed_chapters=count,
            progress=min(0.99, count / total),
        )

    def run_next(self) -> bool:
        job = self.store.next_pending()
        if job is None:
            return False
        if job.max_cost is not None and job.analysis.estimated_cost > job.max_cost:
            self.store.transition(
                job.id, JobStatus.FAILED,
                error=(f"Le coût estimé ({job.analysis.estimated_cost:.2f} €) dépasse "
                       f"le plafond ({job.max_cost:.2f} €)."),
            )
            return True
        try:
            self._validate_credentials(job)
            self.store.transition(job.id, JobStatus.CONVERTING, error="")
            Path(job.output_dir).mkdir(parents=True, exist_ok=True)
            kwargs = {"env": self.environ}
            if os.name != "nt":
                kwargs["start_new_session"] = True
            with self._lock:
                self._current_job_id = job.id
                self._process = self.process_factory(self._command(job), **kwargs)
            while self._process.poll() is None:
                self._update_progress(job)
                if self._stop_event.wait(self.poll_interval):
                    self._terminate_current()
                    break
            return_code = self._process.wait()
            self._update_progress(job)
            if job.id in self._pause_requested or self._stop_event.is_set():
                self._pause_requested.discard(job.id)
                if self.store.get(job.id).status is JobStatus.CONVERTING:
                    self.store.transition(job.id, JobStatus.PAUSED, error="Conversion mise en pause.")
                return True
            if return_code != 0:
                raise RuntimeError(f"Le moteur audio s’est arrêté avec le code {return_code}.")
            tracks = self._tracks(job)
            if not tracks:
                raise RuntimeError("Aucun chapitre audio n’a été produit.")
            job_dir = Path(job.output_dir).parent
            destination_root = Path(job.destination_dir) if job.destination_dir else job_dir
            destination_dir = destination_root / safe_book_name(job.analysis.title)
            destination_dir.mkdir(parents=True, exist_ok=True)
            final_path = destination_dir / f"{safe_book_name(job.analysis.title)}.m4b"
            self.packager.package(tracks, final_path, job.analysis.title, job.analysis.author)
            self.store.transition(
                job.id, JobStatus.COMPLETED, progress=1.0,
                completed_chapters=len(tracks), final_path=str(final_path), error="",
            )
        except Exception as exc:
            current = self.store.get(job.id)
            if current.status is JobStatus.CONVERTING:
                self.store.transition(job.id, JobStatus.FAILED, error=str(exc))
            elif current.status is JobStatus.PENDING:
                self.store.transition(job.id, JobStatus.FAILED, error=str(exc))
        finally:
            with self._lock:
                self._process = None
                self._current_job_id = None
        return True

    def _terminate_current(self) -> None:
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            if os.name != "nt" and getattr(process, "pid", None):
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            pass

    def pause(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job.status is JobStatus.PENDING:
            self.store.transition(job_id, JobStatus.PAUSED, error="Conversion mise en pause.")
            return
        if job.status is not JobStatus.CONVERTING:
            raise InvalidTransition(f"Impossible de mettre en pause un travail {job.status.value}")
        self._pause_requested.add(job_id)
        self._terminate_current()

    def retry(self, job_id: str) -> None:
        self.store.transition(job_id, JobStatus.PENDING, error="")
        self.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._current_job_id:
            self._pause_requested.add(self._current_job_id)
        self._terminate_current()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
