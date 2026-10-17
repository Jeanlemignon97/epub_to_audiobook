from pathlib import Path
from types import SimpleNamespace

from ebooklib import epub

from audiobook_generator.book_parsers.epub_book_parser import EpubBookParser


def make_parser(tmp_path: Path) -> EpubBookParser:
    book = epub.EpubBook()
    book.set_identifier("navigation-test")
    book.set_title("Livre test")
    book.set_language("fr")
    page = epub.EpubHtml(title="Texte", file_name="text.xhtml", lang="fr")
    page.content = """<html><body>
      <p id="cover">Couverture</p>
      <p id="toc">Sommaire</p><p>Chapitre 1 11</p><p>Chapitre 2 22</p>
      <p id="intro"><b>Introduction</b></p><p>Bienvenue.</p>
      <p id="part"><b>Première partie</b></p><p><b>Les bases</b></p>
      <p id="one"><b>Chapitre 1</b></p><p><b>Un nouveau titre</b></p>
      <p>Voici la première ligne</p><p>et sa suite sans ponctuation.</p>
      <p></p><p>Autre paragraphe.</p><p>12</p><p>En 2026, tout change.</p>
      <p><i>N</i> ous avançons.</p>
      <p><a id="p12"></a><i>12</i><b>Première partie : Les bases</b> Et la phrase reprend.</p>
      <p id="two"><b>Chapitre 2</b></p><p>Deuxième texte.</p>
      <p id="index"><b>Index</b></p><p>action 12 obligation 34</p>
    </body></html>"""
    book.add_item(page)
    book.add_item(epub.EpubNcx())
    book.toc = [
        epub.Link("text.xhtml#cover", "Couverture", "cover"),
        epub.Link("text.xhtml#toc", "Sommaire", "toc"),
        epub.Link("text.xhtml#intro", "Introduction", "intro"),
        (epub.Section("Première partie : Les bases", href="text.xhtml#part"), [
            epub.Link("text.xhtml#one", "Chapitre 1 : Un nouveau titre", "one"),
            epub.Link("text.xhtml#two", "Chapitre 2 : La suite", "two"),
        ]),
        epub.Link("text.xhtml#index", "Index", "index"),
    ]
    book.spine = [page]
    path = tmp_path / "book.epub"
    epub.write_epub(str(path), book)
    return EpubBookParser(SimpleNamespace(
        input_file=str(path), newline_mode="double", title_mode="auto",
        remove_endnotes=False, remove_reference_numbers=False,
        search_and_replace_file=None,
    ))


def test_uses_epub_navigation_and_skips_printed_contents(tmp_path: Path):
    chapters = make_parser(tmp_path).get_chapters("@BRK#")
    assert [title for title, _ in chapters] == [
        "Introduction", "Première partie : Les bases",
        "Chapitre 1 : Un nouveau titre", "Chapitre 2 : La suite",
    ]
    assert "Chapitre 1 11" not in " ".join(text for _, text in chapters)
    assert "Deuxième texte" in chapters[-1][1]


def test_preserves_heading_pauses_but_not_pdf_line_wraps(tmp_path: Path):
    chapters = make_parser(tmp_path).get_chapters("@BRK#")
    chapter = chapters[2][1]
    assert "Chapitre 1@BRK#Un nouveau titre@BRK#" in chapter
    assert "Voici la première ligne et sa suite sans ponctuation." in chapter
    assert "sans ponctuation.@BRK#Autre paragraphe." in chapter
    assert " 12 " not in f" {chapter} "
    assert "En 2026, tout change." in chapter
    assert "Nous avançons." in chapter
    assert "Et la phrase reprend." in chapter
    assert "Première partie : Les bases Et la phrase" not in chapter
    assert "action 12 obligation 34" not in " ".join(text for _, text in chapters)
