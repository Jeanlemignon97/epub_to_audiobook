from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from threading import RLock

from audiobook_generator.queue.models import BookAnalysis, BookJob, JobStatus, utc_now


class InvalidTransition(ValueError):
    pass


ALLOWED_TRANSITIONS = {
    JobStatus.ANALYZING: {JobStatus.PENDING, JobStatus.FAILED},
    JobStatus.PENDING: {JobStatus.CONVERTING, JobStatus.PAUSED, JobStatus.FAILED},
    JobStatus.CONVERTING: {JobStatus.COMPLETED, JobStatus.PAUSED, JobStatus.FAILED},
    JobStatus.PAUSED: {JobStatus.PENDING},
    JobStatus.FAILED: {JobStatus.PENDING},
    JobStatus.COMPLETED: set(),
}


class JobStore:
    def __init__(self, database_path: str | Path):
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._create_schema()
        self._recover_interrupted_jobs()

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    source_path TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    voice TEXT NOT NULL,
                    destination_dir TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    author TEXT NOT NULL,
                    chapter_count INTEGER NOT NULL,
                    total_characters INTEGER NOT NULL,
                    estimated_hours REAL NOT NULL,
                    estimated_cost REAL NOT NULL,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL,
                    completed_chapters INTEGER NOT NULL,
                    error TEXT NOT NULL,
                    final_path TEXT NOT NULL,
                    max_cost REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row[1] for row in self._connection.execute("PRAGMA table_info(jobs)")
            }
            if "destination_dir" not in columns:
                self._connection.execute(
                    "ALTER TABLE jobs ADD COLUMN destination_dir TEXT NOT NULL DEFAULT ''"
                )

    def _recover_interrupted_jobs(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE jobs
                SET status = ?, error = ?, updated_at = ?
                WHERE status = ?
                """,
                (
                    JobStatus.PAUSED.value,
                    "Conversion interrompue lors du dernier arrêt.",
                    utc_now(),
                    JobStatus.CONVERTING.value,
                ),
            )

    def add(self, job: BookJob) -> BookJob:
        values = (
            job.id, job.source_path, job.output_dir, job.original_name,
            job.provider, job.model, job.voice, job.destination_dir, job.analysis.title,
            job.analysis.author, job.analysis.chapter_count,
            job.analysis.total_characters, job.analysis.estimated_hours,
            job.analysis.estimated_cost, job.status.value, job.progress,
            job.completed_chapters, job.error, job.final_path, job.max_cost,
            job.created_at, job.updated_at,
        )
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO jobs (
                    id, source_path, output_dir, original_name, provider, model,
                    voice, destination_dir, title, author, chapter_count, total_characters,
                    estimated_hours, estimated_cost, status, progress,
                    completed_chapters, error, final_path, max_cost, created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        return job

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> BookJob:
        return BookJob(
            id=row["id"], source_path=row["source_path"],
            output_dir=row["output_dir"], original_name=row["original_name"],
            provider=row["provider"], model=row["model"], voice=row["voice"],
            destination_dir=row["destination_dir"],
            analysis=BookAnalysis(
                title=row["title"], author=row["author"],
                chapter_count=row["chapter_count"],
                total_characters=row["total_characters"],
                estimated_hours=row["estimated_hours"],
                estimated_cost=row["estimated_cost"],
            ),
            status=JobStatus(row["status"]), progress=row["progress"],
            completed_chapters=row["completed_chapters"], error=row["error"],
            final_path=row["final_path"], max_cost=row["max_cost"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def get(self, job_id: str) -> BookJob:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._row_to_job(row)

    def list_jobs(self) -> list[BookJob]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM jobs ORDER BY sequence"
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def next_pending(self) -> BookJob | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY sequence LIMIT 1",
                (JobStatus.PENDING.value,),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def transition(self, job_id: str, status: JobStatus, error: str = "", **changes) -> BookJob:
        current = self.get(job_id)
        if status not in ALLOWED_TRANSITIONS[current.status]:
            raise InvalidTransition(f"{current.status.value} -> {status.value}")
        return self.update(job_id, status=status, error=error, **changes)

    def update(self, job_id: str, **changes) -> BookJob:
        current = self.get(job_id)
        if "status" in changes and isinstance(changes["status"], JobStatus):
            changes["status"] = changes["status"].value
        allowed = {
            "status", "progress", "completed_chapters", "error", "final_path",
            "max_cost", "voice", "model", "provider",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unsupported fields: {sorted(unknown)}")
        changes["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in changes)
        values = list(changes.values()) + [job_id]
        with self._lock, self._connection:
            self._connection.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?", values
            )
        return self.get(job_id)

    def delete(self, job_id: str) -> None:
        current = self.get(job_id)
        if current.status is JobStatus.CONVERTING:
            raise InvalidTransition("Impossible de supprimer une conversion en cours")
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def close(self) -> None:
        with self._lock:
            self._connection.close()
