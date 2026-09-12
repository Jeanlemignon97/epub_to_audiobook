from pathlib import Path

from audiobook_generator.queue.models import BookAnalysis, BookJob, JobStatus
from audiobook_generator.queue.runner import QueueRunner
from audiobook_generator.queue.store import JobStore


class ResultProcess:
    def __init__(self, command, **kwargs):
        self.returncode = 0
        output = Path(command[3])
        output.mkdir(parents=True, exist_ok=True)
        existing = list(output.glob("*.mp3"))
        index = len(existing) + 1
        (output / f"{index:04d}_Chapitre_{index}.mp3").write_bytes(b"audio")

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class FailingOnceProcess(ResultProcess):
    attempts = 0

    def __init__(self, command, **kwargs):
        super().__init__(command, **kwargs)
        type(self).attempts += 1
        self.returncode = 1 if type(self).attempts == 1 else 0


class RecordingPackager:
    def package(self, tracks, destination, title, author):
        destination = Path(destination)
        destination.write_bytes(b"m4b")
        return destination


def make_job(root: Path, job_id: str):
    directory = root / job_id
    directory.mkdir()
    source = directory / "source.epub"
    source.write_bytes(b"epub")
    return BookJob(
        id=job_id, source_path=str(source), output_dir=str(directory / "chapters"),
        original_name=f"{job_id}.epub", provider="edge", model="",
        voice="fr-FR-DeniseNeural",
        analysis=BookAnalysis(job_id, "Auteur", 1, 1000, 0.02, 0),
    )


def test_two_book_flow_completes_then_retries_without_live_tts(tmp_path: Path):
    store = JobStore(tmp_path / "queue.sqlite3")
    first = make_job(tmp_path, "premier")
    second = make_job(tmp_path, "second")
    store.add(first)
    store.add(second)
    packager = RecordingPackager()

    successful = QueueRunner(store, process_factory=ResultProcess, packager=packager, environ={})
    successful.run_next()
    assert store.get(first.id).status is JobStatus.COMPLETED

    flaky = QueueRunner(store, process_factory=FailingOnceProcess, packager=packager, environ={})
    flaky.run_next()
    assert store.get(second.id).status is JobStatus.FAILED
    cached_tracks = list(Path(second.output_dir).glob("*.mp3"))
    assert cached_tracks

    store.transition(second.id, JobStatus.PENDING, error="")
    flaky.run_next()
    assert store.get(second.id).status is JobStatus.COMPLETED
    assert Path(store.get(second.id).final_path).suffix == ".m4b"
