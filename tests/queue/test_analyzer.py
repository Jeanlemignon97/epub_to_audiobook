from pathlib import Path

import pytest

from audiobook_generator.queue.analyzer import InvalidEpub, analyze_epub


EXAMPLE = Path("examples/The_Life_and_Adventures_of_Robinson_Crusoe.epub")


def test_analyze_example_epub_without_calling_tts():
    analysis = analyze_epub(EXAMPLE, provider="openai", model="gpt-4o-mini-tts")

    assert "Robinson Crusoe" in analysis.title
    assert analysis.author
    assert analysis.chapter_count > 10
    assert analysis.total_characters > 100_000
    assert analysis.estimated_hours > 0
    assert analysis.estimated_cost > 0


def test_invalid_epub_has_readable_error(tmp_path: Path):
    invalid = tmp_path / "broken.epub"
    invalid.write_text("not an epub", encoding="utf-8")

    with pytest.raises(InvalidEpub, match="EPUB valide"):
        analyze_epub(invalid, provider="edge", model="")
