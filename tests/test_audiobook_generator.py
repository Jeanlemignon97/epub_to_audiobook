from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from audiobook_generator.core.audiobook_generator import AudiobookGenerator


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def get_output_file_extension(self):
        return "mp3"

    def text_to_speech(self, text, output_file, audio_tags):
        self.calls += 1
        Path(output_file).write_bytes(b"audio-data")


class FakeBook:
    def get_book_author(self):
        return "Auteur"

    def get_book_title(self):
        return "Livre"


def config(tmp_path: Path):
    return SimpleNamespace(output_text=False, preview=False, output_folder=str(tmp_path))


def test_existing_non_empty_chapter_is_reused(tmp_path: Path):
    provider = FakeProvider()
    generator = AudiobookGenerator(config(tmp_path))
    expected = tmp_path / "0001_Un_chapitre.mp3"
    expected.write_bytes(b"already-generated")

    with patch("audiobook_generator.core.audiobook_generator.get_tts_provider", return_value=provider):
        assert generator.process_chapter(1, "Un chapitre", "Texte", FakeBook())

    assert provider.calls == 0
    assert expected.read_bytes() == b"already-generated"


def test_empty_chapter_is_regenerated_and_replaced_atomically(tmp_path: Path):
    provider = FakeProvider()
    events = []
    generator = AudiobookGenerator(config(tmp_path), progress_callback=events.append)
    expected = tmp_path / "0001_Un_chapitre.mp3"
    expected.touch()

    with patch("audiobook_generator.core.audiobook_generator.get_tts_provider", return_value=provider):
        assert generator.process_chapter(1, "Un chapitre", "Texte", FakeBook())

    assert provider.calls == 1
    assert expected.read_bytes() == b"audio-data"
    assert not list(tmp_path.glob("*.part.mp3"))
    assert events[-1].state == "completed"


def test_cancelled_generator_stops_before_provider_call(tmp_path: Path):
    provider = FakeProvider()
    cancellation = Mock()
    cancellation.is_set.return_value = True
    generator = AudiobookGenerator(config(tmp_path), cancel_event=cancellation)

    with patch("audiobook_generator.core.audiobook_generator.get_tts_provider", return_value=provider):
        assert not generator.process_chapter(1, "Un chapitre", "Texte", FakeBook())

    assert provider.calls == 0
