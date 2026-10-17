from pathlib import Path

from audiobook_generator.queue.models import BookAnalysis, BookJob, JobStatus
from audiobook_generator.queue.runner import QueueRunner
from audiobook_generator.queue.store import JobStore


def make_job(tmp_path: Path, *, cost=0.5, max_cost=None, destination_dir="") -> BookJob:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    source = job_dir / "source.epub"
    source.write_bytes(b"epub")
    return BookJob(
        id="job", source_path=str(source), output_dir=str(job_dir / "chapters"),
        original_name="book.epub", provider="edge", model="", voice="fr-FR-DeniseNeural",
        analysis=BookAnalysis("Mon livre", "Auteur", 1, 10_000, 0.2, cost),
        max_cost=max_cost, destination_dir=str(destination_dir),
    )


class FinishedProcess:
    returncode = 0

    def __init__(self, command, **kwargs):
        output_dir = Path(command[3])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "0001_Introduction.mp3").write_bytes(b"audio")

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.returncode = -15


class FakePackager:
    def package(self, tracks, destination, title, author):
        destination = Path(destination)
        destination.write_bytes(b"m4b")
        self.tracks = list(tracks)
        return destination


def test_runner_completes_job_and_records_m4b(tmp_path: Path):
    store = JobStore(tmp_path / "queue.sqlite3")
    store.add(make_job(tmp_path))
    packager = FakePackager()
    runner = QueueRunner(store, process_factory=FinishedProcess, packager=packager, environ={})

    runner.run_next()

    job = store.get("job")
    assert job.status is JobStatus.COMPLETED
    assert job.progress == 1
    assert Path(job.final_path).read_bytes() == b"m4b"
    assert packager.tracks[0].title == "Introduction"


def test_runner_rejects_job_over_cost_ceiling(tmp_path: Path):
    store = JobStore(tmp_path / "queue.sqlite3")
    store.add(make_job(tmp_path, cost=5, max_cost=2))
    runner = QueueRunner(store, process_factory=FinishedProcess, packager=FakePackager(), environ={})

    runner.run_next()

    job = store.get("job")
    assert job.status is JobStatus.FAILED
    assert "plafond" in job.error.lower()


def test_runner_writes_final_m4b_to_selected_destination(tmp_path: Path):
    destination = tmp_path / "mes livres audio"
    store = JobStore(tmp_path / "queue.sqlite3")
    store.add(make_job(tmp_path, destination_dir=destination))
    runner = QueueRunner(
        store, process_factory=FinishedProcess, packager=FakePackager(), environ={}
    )

    runner.run_next()

    final_path = Path(store.get("job").final_path)
    assert final_path.parent == destination / "Mon_livre"
    assert final_path.read_bytes() == b"m4b"


def test_retry_returns_failed_job_to_pending(tmp_path: Path):
    store = JobStore(tmp_path / "queue.sqlite3")
    store.add(make_job(tmp_path))
    store.transition("job", JobStatus.FAILED, error="Erreur réseau")
    runner = QueueRunner(store, process_factory=FinishedProcess, packager=FakePackager(), environ={})
    runner.start = lambda: None

    runner.retry("job")

    assert store.get("job").status is JobStatus.PENDING
