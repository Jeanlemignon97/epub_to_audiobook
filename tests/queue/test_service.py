from pathlib import Path

import pytest

from audiobook_generator.queue.models import JobStatus
from audiobook_generator.queue.service import QueueService


EXAMPLE = Path("examples/The_Life_and_Adventures_of_Robinson_Crusoe.epub")


class IdleRunner:
    def __init__(self, store, **kwargs):
        self.store = store
        self.started = False

    def start(self):
        self.started = True

    def pause(self, job_id):
        self.store.transition(job_id, JobStatus.PAUSED)

    def retry(self, job_id):
        self.store.transition(job_id, JobStatus.PENDING)

    def stop(self):
        pass


def test_service_imports_multiple_epubs_into_private_directories(tmp_path: Path):
    service = QueueService(tmp_path / "data", runner_factory=IdleRunner)

    jobs = service.add_books(
        [EXAMPLE, EXAMPLE], provider="edge", model="", voice="fr-FR-DeniseNeural"
    )

    assert len(jobs) == 2
    assert jobs[0].id != jobs[1].id
    assert all(Path(job.source_path).is_file() for job in jobs)
    assert all(job.status is JobStatus.PENDING for job in jobs)
    assert len(service.list_jobs()) == 2


def test_service_retry_starts_runner(tmp_path: Path):
    service = QueueService(tmp_path / "data", runner_factory=IdleRunner)
    job = service.add_books([EXAMPLE], "edge", "", "fr-FR-DeniseNeural")[0]
    service.store.transition(job.id, JobStatus.FAILED, error="Erreur")

    service.retry(job.id)

    assert service.store.get(job.id).status is JobStatus.PENDING
    assert service.runner.started


def test_service_limits_each_import_to_three_books(tmp_path: Path):
    service = QueueService(tmp_path / "data", runner_factory=IdleRunner)

    with pytest.raises(ValueError, match="maximum 3 EPUB"):
        service.add_books([EXAMPLE] * 4, "edge", "", "fr-FR-DeniseNeural")
