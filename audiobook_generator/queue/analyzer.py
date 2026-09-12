from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

from audiobook_generator.book_parsers.epub_book_parser import EpubBookParser
from audiobook_generator.config.general_config import GeneralConfig
from audiobook_generator.queue.models import BookAnalysis
from audiobook_generator.tts_providers.openai_tts_provider import get_price


class InvalidEpub(ValueError):
    pass


def _parser_config(path: Path) -> GeneralConfig:
    return GeneralConfig(SimpleNamespace(
        input_file=str(path), title_mode="auto", newline_mode="double",
        remove_endnotes=False, remove_reference_numbers=False,
        search_and_replace_file=None,
    ))


def estimate_cost(total_characters: int, provider: str, model: str) -> float:
    if provider != "openai":
        return 0.0
    selected_model = model or "gpt-4o-mini-tts"
    return round(math.ceil(total_characters / 1000) * get_price(selected_model), 2)


def analyze_epub(path: str | Path, provider: str, model: str) -> BookAnalysis:
    source = Path(path)
    if source.suffix.lower() != ".epub" or not source.is_file():
        raise InvalidEpub("Le fichier sélectionné n’est pas un EPUB valide.")
    try:
        parser = EpubBookParser(_parser_config(source))
        chapters = [chapter for chapter in parser.get_chapters("   ") if chapter[1].strip()]
    except Exception as exc:
        raise InvalidEpub("Le fichier sélectionné n’est pas un EPUB valide.") from exc
    if not chapters:
        raise InvalidEpub("Cet EPUB ne contient aucun texte lisible.")
    total_characters = sum(len(text) for _, text in chapters)
    return BookAnalysis(
        title=parser.get_book_title().strip() or source.stem,
        author=parser.get_book_author().strip() or "Auteur inconnu",
        chapter_count=len(chapters),
        total_characters=total_characters,
        estimated_hours=round(total_characters / 900 / 60, 2),
        estimated_cost=estimate_cost(total_characters, provider, model),
    )
