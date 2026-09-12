from __future__ import annotations

import shutil
import uuid
import os
from pathlib import Path

from audiobook_generator.queue.analyzer import analyze_epub
from audiobook_generator.queue.models import BookJob
from audiobook_generator.queue.runner import QueueRunner
from audiobook_generator.queue.store import JobStore


class QueueService:
    def __init__(self, data_dir: str | Path = "audiobook_data", runner_factory=QueueRunner):
        self.data_dir = Path(data_dir).resolve()
        self.jobs_dir = self.data_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.data_dir / "queue.sqlite3")
        self.runner = runner_factory(self.store)

    def add_books(
        self,
        files,
        provider: str,
        model: str,
        voice: str,
        max_cost: float | None = None,
    ) -> list[BookJob]:
        jobs = []
        for uploaded in files or []:
            source = Path(uploaded if isinstance(uploaded, (str, os.PathLike)) else uploaded.name)
            job_id = str(uuid.uuid4())
            job_dir = self.jobs_dir / job_id
            job_dir.mkdir(parents=True, exist_ok=False)
            private_source = job_dir / "source.epub"
            try:
                shutil.copy2(source, private_source)
                analysis = analyze_epub(private_source, provider, model)
                job = BookJob(
                    id=job_id, source_path=str(private_source),
                    output_dir=str(job_dir / "chapters"), original_name=source.name,
                    provider=provider, model=model, voice=voice,
                    analysis=analysis, max_cost=max_cost,
                )
                self.store.add(job)
                jobs.append(job)
            except Exception:
                shutil.rmtree(job_dir, ignore_errors=True)
                raise
        return jobs

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

    def close(self) -> None:
        self.runner.stop()
        self.store.close()
