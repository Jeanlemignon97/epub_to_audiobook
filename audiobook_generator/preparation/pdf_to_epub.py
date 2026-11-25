from __future__ import annotations

import html
import re
import shutil
from pathlib import Path

from ebooklib import epub


class PdfConversionError(ValueError):
    pass


def _pymupdf():
    try:
        import pymupdf
    except ImportError as exc:
        raise PdfConversionError("PyMuPDF manque. Installez les dépendances du projet.") from exc
    return pymupdf


def inspect_pdf(path: str | Path) -> dict:
    source = Path(path)
    if source.suffix.lower() != ".pdf" or not source.is_file():
        raise PdfConversionError("Sélectionnez un fichier PDF valide.")
    try:
        with _pymupdf().open(source) as pdf:
            if pdf.page_count == 0:
                raise PdfConversionError("Ce PDF ne contient aucune page.")
            pages = [pdf[i] for i in sorted({pdf.page_count // 4, pdf.page_count // 2, pdf.page_count * 3 // 4})]
            samples = [page.get_text(sort=True).strip() for page in pages]
            return {
                "title": (pdf.metadata or {}).get("title") or source.stem,
                "author": (pdf.metadata or {}).get("author") or "",
                "pages": pdf.page_count,
                "needs_ocr": any(
                    len(sample) < 40 and bool(page.get_images(full=True))
                    for sample, page in zip(samples, pages)
                ),
                "preview": next((sample[:450] for sample in samples if sample), ""),
            }
    except PdfConversionError:
        raise
    except Exception as exc:
        raise PdfConversionError("Le PDF est illisible ou protégé.") from exc


def _page_blocks(page, number: int, skip_cover_ocr: bool = False) -> list[str]:
    text = page.get_text(sort=True)
    if len(text.strip()) < 40 and page.get_images(full=True):
        if skip_cover_ocr:
            return []
        if not shutil.which("tesseract"):
            raise PdfConversionError(
                f"La page {number} semble scannée. Installez Tesseract avec les langues français et anglais, puis réessayez."
            )
        try:
            text_page = page.get_textpage_ocr(language="fra+eng", dpi=200, full=True)
            text = page.get_text(textpage=text_page, sort=True)
        except Exception as exc:
            raise PdfConversionError(f"La reconnaissance de texte a échoué à la page {number}.") from exc
    if not text.strip():
        return []  # Blank page, often used between printed sections.
    return [
        re.sub(r"\s+", " ", block).strip()
        for block in re.split(r"\n\s*\n", text)
        if re.sub(r"\s+", " ", block).strip()
        and not re.fullmatch(r"\d{1,4}", block.strip())
    ]


def convert_pdf(path: str | Path, destination: str | Path, progress=lambda done, total: None) -> Path:
    source = Path(path)
    details = inspect_pdf(source)
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    book = epub.EpubBook()
    book.set_identifier(f"pdf-to-epub:{source.stem}:{source.stat().st_size}")
    title = details["title"].strip() or source.stem
    book.set_title(title)
    book.set_language("fr" if re.search(r"[éèàùç]", details["preview"], re.IGNORECASE) else "en")
    if details["author"].strip():
        book.add_author(details["author"].strip())
    chapters = []
    with _pymupdf().open(source) as pdf:
        # A real PDF outline gives the reader meaningful chapter markers. Otherwise
        # group pages so a 300-page book does not become 300 tiny chapters.
        outline = [(int(page), name.strip()) for level, name, page, *_ in pdf.get_toc() if level == 1 and page > 0 and name.strip()]
        starts = {page: name for page, name in outline}
        first_substantial_page = next(
            (i for i, page in enumerate(pdf) if len(page.get_text(sort=True).strip()) > 80),
            None,
        )
        groups: list[tuple[str, list[tuple[int, list[str]]]]] = []
        readable_pages = 0
        for index, page in enumerate(pdf):
            number = index + 1
            if not groups or number in starts or (not outline and len(groups[-1][1]) >= 8):
                groups.append((starts.get(number, f"Pages {number} à {min(number + 7, pdf.page_count)}"), []))
            blocks = _page_blocks(
                page, number,
                skip_cover_ocr=first_substantial_page is not None and index < first_substantial_page,
            )
            readable_pages += bool(blocks)
            groups[-1][1].append((number, blocks))
            progress(number, pdf.page_count)
        if not readable_pages:
            raise PdfConversionError("Ce PDF ne contient aucun texte lisible.")
    for index, (heading, pages) in enumerate(groups, start=1):
        body = [f"<h1>{html.escape(heading)}</h1>"]
        for _, blocks in pages:
            body.extend(f"<p>{html.escape(block)}</p>" for block in blocks)
        chapter = epub.EpubHtml(title=heading, file_name=f"section_{index:04}.xhtml", lang="fr")
        chapter.content = f"<html xmlns='http://www.w3.org/1999/xhtml'><head><title>{html.escape(heading)}</title></head><body>{''.join(body)}</body></html>"
        book.add_item(chapter)
        chapters.append(chapter)
    book.toc = chapters
    book.spine = ["nav", *chapters]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    temporary = output.with_suffix(".partial.epub")
    try:
        epub.write_epub(str(temporary), book)
        # Validate before exposing the final file to Books.
        checked = epub.read_epub(str(temporary))
        if not checked.toc:
            raise PdfConversionError("L’EPUB créé n’a pas de navigation.")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
