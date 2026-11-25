import shutil
from pathlib import Path

import ebooklib
import pymupdf
import pytest
from ebooklib import epub
from lxml import etree

from audiobook_generator.preparation.pdf_to_epub import (
    PdfConversionError,
    convert_pdf,
    inspect_pdf,
)
from audiobook_generator.preparation.service import PreparationService
from audiobook_generator.preparation.translate_epub import inspect_epub, translate_epub
from audiobook_generator.ui.preparation_view import render_preparations


def make_pdf(path: Path):
    pdf = pymupdf.open()
    for number in range(2):
        page = pdf.new_page()
        page.insert_text((50, 70), f"Chapter {number + 1}\nThis is a real paragraph of English text that should be present in the converted EPUB.")
    pdf.set_metadata({"title": "A sample book", "author": "Example Writer"})
    pdf.save(path)


def make_epub(path: Path):
    book = epub.EpubBook()
    book.set_identifier("test-english-book")
    book.set_title("A Sample Book")
    book.set_language("en")
    chapter = epub.EpubHtml(title="First Chapter", file_name="first.xhtml", lang="en")
    chapter.content = "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>First Chapter</title></head><body><h1 id='start'>First Chapter</h1><p>Hello <em>dear</em> reader. This sentence has enough words to translate.</p><a href='#start'>Go back</a></body></html>"
    book.add_item(chapter)
    book.toc = [epub.Link("first.xhtml#start", "First Chapter", "first")]
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNav())
    book.add_item(epub.EpubNcx())
    epub.write_epub(path, book)


class FakeTranslator:
    def __init__(self):
        self.calls = 0

    def translate(self, texts):
        self.calls += 1
        return [f"FR {text}" for text in texts]


def test_pdf_to_epub_has_text_and_navigation(tmp_path):
    source = tmp_path / "source.pdf"
    target = tmp_path / "A sample book.epub"
    make_pdf(source)
    assert inspect_pdf(source)["pages"] == 2
    convert_pdf(source, target)
    book = epub.read_epub(target)
    assert len(book.toc) == 1
    assert book.get_metadata("DC", "title")[0][0] == "A sample book"
    documents = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
    assert any(b"real paragraph" in item.get_content() for item in documents)
    assert target.is_file() and target.stat().st_size > 0


def test_scanned_pdf_reports_ocr_requirement(tmp_path, monkeypatch):
    source = tmp_path / "scan.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page()
    picture = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), 0)
    picture.clear_with(255)
    page.insert_image(pymupdf.Rect(40, 40, 140, 140), stream=picture.tobytes("png"))
    pdf.save(source)
    monkeypatch.setattr("audiobook_generator.preparation.pdf_to_epub.shutil.which", lambda _: None)
    with pytest.raises(PdfConversionError, match="Tesseract"):
        convert_pdf(source, tmp_path / "result.epub")


def test_epub_translation_preserves_anchor_and_resumes_from_checkpoint(tmp_path):
    source = tmp_path / "source.epub"
    target = tmp_path / "french.epub"
    make_epub(source)
    info = inspect_epub(source)
    assert info["characters"] > 30
    fake = FakeTranslator()
    translate_epub(source, target, translator=fake, checkpoint_dir=tmp_path / "checkpoints")
    first_calls = fake.calls
    book = epub.read_epub(target)
    assert book.get_metadata("DC", "language")[0][0] == "fr"
    assert book.get_metadata("DC", "title")[0][0] == "FR A Sample Book"
    assert book.toc[0].title == "FR First Chapter"
    chapter = book.get_item_with_href("first.xhtml")
    root = etree.fromstring(chapter.get_content())
    assert root.xpath("//*[@id='start']")
    assert root.xpath("//*[@href='#start']")
    assert b"FR Hello" in chapter.get_content()
    translate_epub(source, target, translator=fake, checkpoint_dir=tmp_path / "checkpoints")
    assert fake.calls == first_calls + 2  # title and TOC still need translation


def test_preparation_queue_persists_and_never_saves_api_key(tmp_path, monkeypatch):
    source = tmp_path / "source.epub"
    make_epub(source)
    service = PreparationService(tmp_path / "data")
    monkeypatch.setattr(service, "start", lambda: None)
    job = service.add(source, "translate_en_fr", tmp_path / "exports", confirmed=True, api_key="private-test-key")
    assert service.list_jobs()[0]["id"] == job["id"]
    assert "private-test-key" not in service.database.read_bytes().decode(errors="ignore")
    assert job["title"] in Path(job["result_path"]).parent.name.replace("_", " ")
    assert render_preparations(service.list_jobs()).count("source.epub") == 0
    service._update(job["id"], status="running")
    reopened = PreparationService(tmp_path / "data")
    assert reopened.list_jobs()[0]["status"] == "paused"


def test_pdf_job_finishes_in_named_folder_with_named_download(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    make_pdf(source)
    service = PreparationService(tmp_path / "data")
    monkeypatch.setattr(service, "start", lambda: None)
    job = service.add(source, "pdf_to_epub", tmp_path / "exports")
    assert service.run_next()
    finished = service.list_jobs()[0]
    assert finished["status"] == "completed"
    assert finished["progress"] == 1.0
    assert Path(finished["result_path"]).is_file()
    assert Path(finished["result_path"]).name == "A_sample_book.epub"
    assert (service.data_dir / "preparations" / job["id"] / "A_sample_book.epub").is_file()
    card = render_preparations([finished], str(tmp_path / "exports"), "/Users/reader/Documents")
    assert "/Users/reader/Documents/A_sample_book-" in card
    assert str(tmp_path / "exports") not in card


def test_translation_job_uses_memory_key_and_completes_without_browser(tmp_path, monkeypatch):
    source = tmp_path / "source.epub"
    make_epub(source)
    service = PreparationService(tmp_path / "data")
    monkeypatch.setattr(service, "start", lambda: None)
    used = {}

    def fake_translate(path, destination, api_key, progress, checkpoint_dir):
        used["key"] = api_key
        shutil.copy2(path, destination)
        progress(1, 1)

    monkeypatch.setattr("audiobook_generator.preparation.service.translate_epub", fake_translate)
    job = service.add(source, "translate_en_fr", tmp_path / "exports", confirmed=True, api_key="memory-only-key")
    assert service.run_next()
    assert used["key"] == "memory-only-key"
    assert service.list_jobs()[0]["status"] == "completed"
    assert Path(job["result_path"]).is_file()
    assert "memory-only-key" not in service.database.read_bytes().decode(errors="ignore")
