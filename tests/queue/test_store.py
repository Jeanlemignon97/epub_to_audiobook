from pathlib import Path

import pytest

from audiobook_generator.queue.models import BookAnalysis, BookJob, JobStatus
from audiobook_generator.queue.store import InvalidTransition, JobStore


def sample_job(job_id: str = "job-1") -> BookJob:
    return BookJob(
        id=job_id,
        source_path=f"/tmp/{job_id}.epub",
        output_dir=f"/tmp/{job_id}",
        original_name=f"{job_id}.epub",
        provider="openai",
        model="gpt-4o-mini-tts",
        voice="coral",
        analysis=BookAnalysis(
            title="Livre test",
            author="Auteur test",
            chapter_count=12,
            total_characters=90_000,
            estimated_hours=1.67,
            estimated_cost=0.27,
        ),
    )


def test_store_persists_jobs_and_preserves_fifo(tmp_path: Path):
    database = tmp_path / "queue.sqlite3"
    store = JobStore(database)
    store.add(sample_job("first"))
    store.add(sample_job("second"))
    store.close()

    reopened = JobStore(database)
    assert [job.id for job in reopened.list_jobs()] == ["first", "second"]
    assert reopened.next_pending().id == "first"


def test_store_enforces_status_transitions(tmp_path: Path):
    store = JobStore(tmp_path / "queue.sqlite3")
    store.add(sample_job())

    converting = store.transition("job-1", JobStatus.CONVERTING)
    assert converting.status is JobStatus.CONVERTING

    with pytest.raises(InvalidTransition):
        store.transition("job-1", JobStatus.ANALYZING)

    failed = store.transition("job-1", JobStatus.FAILED, error="Réseau indisponible")
    assert failed.error == "Réseau indisponible"
    assert store.transition("job-1", JobStatus.PENDING).status is JobStatus.PENDING


def test_interrupted_jobs_are_recovered_as_paused(tmp_path: Path):
    database = tmp_path / "queue.sqlite3"
    store = JobStore(database)
    store.add(sample_job())
    store.transition("job-1", JobStatus.CONVERTING)
    store.close()

    reopened = JobStore(database)
    job = reopened.get("job-1")
    assert job.status is JobStatus.PAUSED
    assert "interrompue" in job.error.lower()
