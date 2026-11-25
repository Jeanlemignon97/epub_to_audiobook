"""Optional real-browser layout smoke; run with Playwright and desktop Chrome."""

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pymupdf
import pytest
from ebooklib import epub

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


@pytest.mark.skipif(not CHROME.is_file(), reason="Chrome is needed for visual smoke")
def test_preparation_ui_reflows_and_keeps_actions_reachable(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env["AUDIOBOOK_DATA_DIR"] = str(tmp_path / "data")
    env["AUDIOBOOK_OUTPUT_DIR"] = str(tmp_path / "exports")
    env["OPENAI_API_KEY"] = ""
    server = subprocess.Popen(
        [sys.executable, "main_ui.py", "--port", str(port)],
        cwd=Path(__file__).parents[2], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(url, timeout=1).close()
                break
            except (OSError, urllib.error.URLError):
                if server.poll() is not None:
                    pytest.fail("Development server exited before browser test")
                time.sleep(0.1)
        else:
            pytest.fail("Development server did not start")
        with playwright.sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=str(CHROME), headless=True)
            try:
                for width, height in [(320, 640), (390, 844), (600, 900), (1024, 1366), (900, 600)]:
                    for scale in (1.0, 1.3):
                        page = browser.new_page(viewport={"width": width, "height": height})
                        page.goto(url, wait_until="domcontentloaded")
                        page.get_by_role("tab", name="Préparer un EPUB").click()
                        page.evaluate("scale => document.documentElement.style.fontSize = `${16 * scale}px`", scale)
                        metrics = page.evaluate("""() => ({
                          scroll: document.body.scrollWidth,
                          viewport: innerWidth,
                          heading: document.querySelector('h1').getBoundingClientRect().right,
                          actions: [...document.querySelectorAll('button')]
                            .filter(x => x.offsetParent && ['Analyser le livre', 'Lancer la préparation'].includes(x.innerText))
                            .map(x => ({height: x.getBoundingClientRect().height, width: x.getBoundingClientRect().width}))
                        })""")
                        assert metrics["scroll"] <= metrics["viewport"], (width, height, scale, metrics)
                        assert metrics["heading"] <= width
                        assert len(metrics["actions"]) == 2
                        assert all(action["height"] >= 48 and action["width"] >= 48 for action in metrics["actions"])
                        button = page.get_by_role("button", name="Lancer la préparation")
                        button.scroll_into_view_if_needed()
                        assert button.is_visible()
                        page.close()

                page = browser.new_page(viewport={"width": 320, "height": 640})
                page.goto(url, wait_until="domcontentloaded")
                page.get_by_role("tab", name="Préparer un EPUB").click()
                page.get_by_role("button", name="Lancer la préparation").click()
                page.get_by_text("Choisissez d’abord un PDF ou un EPUB.").wait_for()
                source = tmp_path / "sample.pdf"
                pdf = pymupdf.open()
                pdf.new_page().insert_text((50, 80), "A sample book\nEnough readable text for the PDF conversion browser test and a valid EPUB.")
                pdf.set_metadata({"title": "Sample browser book"})
                pdf.save(source)
                page.locator('input[type="file"]').last.set_input_files(str(source))
                page.locator('.file-preview .filename[aria-label="sample.pdf"]').wait_for()
                page.get_by_role("button", name="Analyser le livre").click()
                page.get_by_text("Sample browser book").first.wait_for()
                page.get_by_role("button", name="Lancer la préparation").click()
                page.get_by_text("Prêt", exact=True).wait_for(timeout=30000)
                page.get_by_text("Télécharger l’EPUB").wait_for()
                assert page.locator('.file-preview .download-link').last.get_attribute("download") == "Sample_browser_book.epub"
                assert page.evaluate("document.body.scrollWidth <= innerWidth")

                english = tmp_path / "english.epub"
                book = epub.EpubBook()
                book.set_identifier("browser-english")
                book.set_title("English sample")
                book.set_language("en")
                chapter = epub.EpubHtml(title="Chapter One", file_name="one.xhtml", lang="en")
                chapter.content = "<html xmlns='http://www.w3.org/1999/xhtml'><body><h1>Chapter One</h1><p>This English paragraph has enough words to estimate the translation cost.</p></body></html>"
                book.add_item(chapter)
                book.toc = [chapter]
                book.spine = ["nav", chapter]
                book.add_item(epub.EpubNav())
                book.add_item(epub.EpubNcx())
                epub.write_epub(english, book)
                page.get_by_text("Anglais → français", exact=True).click()
                page.locator('.file-preview .filename[aria-label="sample.pdf"]').wait_for(state="hidden")
                page.wait_for_timeout(500)  # let Gradio's kind-change event finish
                page.locator('input[type="file"]').last.set_input_files(str(english))
                page.locator('.file-preview .filename[aria-label="english.epub"]').wait_for(state="attached")
                page.wait_for_timeout(500)  # let Gradio's file-change event clear stale analysis
                page.get_by_role("button", name="Analyser le livre").click()
                page.get_by_text("Coût estimé", exact=False).wait_for()
                page.get_by_role("checkbox", name="J’ai lu l’estimation").check()
                page.get_by_role("button", name="Lancer la préparation").click()
                page.get_by_text("Ajoutez votre clé API OpenAI", exact=False).wait_for()
                page.set_viewport_size({"width": 320, "height": 400})
                page.get_by_role("button", name="Lancer la préparation").scroll_into_view_if_needed()
                assert page.get_by_role("button", name="Lancer la préparation").is_visible()
                page.close()
            finally:
                browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
