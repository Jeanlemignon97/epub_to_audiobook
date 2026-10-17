from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path

from audiobook_generator.queue.analyzer import analyze_epub
from audiobook_generator.queue.models import BookJob, JobStatus
from audiobook_generator.queue.naming import safe_book_name
from audiobook_generator.queue.runner import QueueRunner
from audiobook_generator.queue.store import JobStore


class QueueService:
    MAX_FILES_PER_IMPORT = 3

    def __init__(self, data_dir: str | Path = "audiobook_data", runner_factory=QueueRunner):
        self.data_dir = Path(data_dir).resolve()
        self.jobs_dir = self.data_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.data_dir / "queue.sqlite3")
        self.runner = runner_factory(self.store)
        self._closed = False

    def add_books(
        self,
        files,
        provider: str,
        model: str,
        voice: str,
        max_cost: float | None = None,
        destination_dir: str | Path = "",
    ) -> list[BookJob]:
        files = list(files or [])
        if len(files) > self.MAX_FILES_PER_IMPORT:
            raise ValueError(
                f"Vous pouvez ajouter au maximum {self.MAX_FILES_PER_IMPORT} EPUB à la fois."
            )
        destination = (
            str(Path(destination_dir).expanduser().resolve()) if destination_dir else ""
        )
        if destination:
            Path(destination).mkdir(parents=True, exist_ok=True)
        jobs = []
        for uploaded in files:
            source = Path(uploaded if isinstance(uploaded, (str, os.PathLike)) else uploaded.name)
            job_id = str(uuid.uuid4())
            analysis = analyze_epub(source, provider, model)
            job_dir = self.jobs_dir / f"{safe_book_name(analysis.title)}-{job_id[:8]}"
            job_dir.mkdir(parents=True, exist_ok=False)
            private_source = job_dir / "source.epub"
            try:
                shutil.copy2(source, private_source)
                job = BookJob(
                    id=job_id, source_path=str(private_source),
                    output_dir=str(job_dir / "chapters"), original_name=source.name,
                    provider=provider, model=model, voice=voice,
                    analysis=analysis, max_cost=max_cost,
                    destination_dir=destination,
                )
                self.store.add(job)
                jobs.append(job)
            except Exception:
                shutil.rmtree(job_dir, ignore_errors=True)
                raise
        return jobs

    def set_openai_api_key(self, api_key: str) -> None:
        key = (api_key or "").strip()
        if not key:
            return
        if not hasattr(self.runner, "environ"):
            self.runner.environ = {}
        self.runner.environ["OPENAI_API_KEY"] = key

    def list_jobs(self):
        return self.store.list_jobs()

    def start(self) -> None:
        self.runner.start()

    def pause(self, job_id: str) -> None:
        self.runner.pause(job_id)

    def resume(self, job_id: str) -> None:
        self.runner.retry(job_id)
        self.runner.start()

    def retry(self, job_id: str) -> None:
        self.runner.retry(job_id)
        self.runner.start()

    def delete(self, job_id: str) -> None:
        job = self.store.get(job_id)
        self.store.delete(job_id)
        shutil.rmtree(Path(job.output_dir).parent, ignore_errors=True)

    def remove_duplicates(self) -> int:
        groups: dict[tuple[str, str, str, str, str], list[BookJob]] = {}
        for job in self.store.list_jobs():
            if job.status is JobStatus.FAILED:
                continue
            try:
                with Path(job.source_path).open("rb") as source:
                    digest = hashlib.file_digest(source, "sha256").hexdigest()
            except FileNotFoundError:
                continue
            key = (digest, job.provider, job.model, job.voice, job.destination_dir)
            groups.setdefault(key, []).append(job)

        removed = 0
        for jobs in groups.values():
            keeper = next(
                (job for job in jobs if job.status is JobStatus.CONVERTING),
                next((job for job in jobs if job.status is JobStatus.COMPLETED), jobs[0]),
            )
            for job in jobs:
                if job.id == keeper.id:
                    continue
                current = self.store.get(job.id)
                if current.status in {JobStatus.PENDING, JobStatus.PAUSED} and not current.completed_chapters:
                    self.delete(job.id)
                    removed += 1
        return removed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.runner.stop()
        self.store.close()
