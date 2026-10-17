from pathlib import Path
from types import SimpleNamespace

from ebooklib import epub

from audiobook_generator.book_parsers.epub_book_parser import EpubBookParser, TextBlock


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
      <p><b>Chapitre 1 : Un nouveau titre</b></p>
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


def test_navigation_can_seek_nested_heading_on_same_epub_page(tmp_path: Path):
    parser = make_parser(tmp_path)
    parser.book.toc = [
        epub.Link("text.xhtml#intro", "Introduction", "intro"),
        (epub.Section("Première partie : Les bases", href="text.xhtml#part"), [
            (epub.Section("Chapitre 1 : Un nouveau titre", href="text.xhtml#one"), [
                epub.Link("text.xhtml#one", "Un nouveau titre", "subheading"),
            ]),
            epub.Link("text.xhtml#two", "Chapitre 2 : La suite", "two"),
        ]),
    ]

    chapters = parser.get_chapters("@BRK#")

    assert [title for title, _ in chapters] == [
        "Introduction", "Première partie : Les bases",
        "Chapitre 1 : Un nouveau titre", "Un nouveau titre",
        "Chapitre 2 : La suite",
    ]
    assert chapters[2][1] == "Chapitre 1"
    assert chapters[3][1].startswith("Un nouveau titre@BRK#Voici la première ligne")


def test_short_breath_separates_unpunctuated_lines(tmp_path: Path):
    chapter = make_parser(tmp_path).get_chapters("@BRK#", "@SBRK#")[2][1]

    assert "Voici la première ligne@SBRK#et sa suite sans ponctuation." in chapter
    assert "Chapitre 1@BRK#Un nouveau titre@BRK#" in chapter


def test_break_marker_does_not_trim_first_letter_of_heading(tmp_path: Path):
    parser = make_parser(tmp_path)
    text = parser._clean_text(
        [TextBlock("text.xhtml", (), "Récapitulons", True)], "@BRK#", "@SBRK#"
    )

    assert text == "Récapitulons"


def test_prefers_original_heading_over_later_repeated_page_header():
    blocks = [
        TextBlock("text.xhtml", ("p41",), "Chapitre 2", True),
        TextBlock("text.xhtml", (), "Le meil eur des placements ?", True),
        TextBlock("text.xhtml", (), "Le contenu à écouter.", False),
        TextBlock("text.xhtml", ("p43",), "Chapitre 2 : Le meilleur des placements ?", True),
    ]

    assert EpubBookParser._heading_position(
        blocks, "Chapitre 2 : Le meilleur des placements ?", 0, len(blocks)
    ) == 0
