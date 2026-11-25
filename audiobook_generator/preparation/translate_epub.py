from __future__ import annotations

import hashlib
import json
import math
import re
import time
from pathlib import Path

import ebooklib
from ebooklib import epub
from lxml import etree
from openai import OpenAI

TRANSLATION_MODEL = "gpt-4.1-mini"
DC = "http://purl.org/dc/elements/1.1/"
SKIP_TAGS = {"script", "style"}


class TranslationError(ValueError):
    pass


def inspect_epub(path: str | Path) -> dict:
    source = Path(path)
    if source.suffix.lower() != ".epub" or not source.is_file():
        raise TranslationError("Sélectionnez un fichier EPUB valide.")
    try:
        book = epub.read_epub(str(source), {"ignore_ncx": True})
        count = 0
        preview = ""
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            if isinstance(item, epub.EpubNav):
                continue
            root = etree.fromstring(item.get_content())
            for node in root.xpath("//*[local-name()='body']//text()"):
                value = str(node).strip()
                if value:
                    count += len(value)
                    if not preview:
                        preview = value[:450]
        if count < 30:
            raise TranslationError("Cet EPUB ne contient pas assez de texte lisible.")
        title = book.get_metadata("DC", "title")
        language = book.get_metadata("DC", "language")
        return {
            "title": title[0][0] if title else source.stem,
            "language": language[0][0] if language else "",
            "characters": count,
            "preview": preview,
            "estimated_usd": estimate_translation_cost(count),
        }
    except TranslationError:
        raise
    except Exception as exc:
        raise TranslationError("L’EPUB est illisible ou protégé.") from exc


def estimate_translation_cost(characters: int) -> float:
    # Indicative standard rates for gpt-4.1-mini at implementation time:
    # $0.40 / 1M input tokens and $1.60 / 1M output tokens. Tokenization,
    # prompts, and French expansion vary; the 1.5 multiplier adds headroom.
    tokens = characters / 3.5
    return max(0.001, math.ceil(1.5 * tokens * (0.40 + 1.60 * 1.2) / 1_000_000 * 1000) / 1000)


class OpenAITranslator:
    def __init__(self, api_key: str):
        if not api_key:
            raise TranslationError("Ajoutez une clé API OpenAI pour traduire le livre.")
        self.client = OpenAI(api_key=api_key)

    def translate(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        payload = json.dumps({"segments": texts}, ensure_ascii=False)
        for attempt in range(3):
            try:
                response = self.client.chat.completions.create(
                    model=TRANSLATION_MODEL,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": (
                            "Traduis chaque segment de l'anglais vers un français naturel et fidèle. "
                            "Conserve noms propres, chiffres, citations et ordre. "
                            "Réponds uniquement par un objet JSON avec la clé 'translations', "
                            "un tableau de chaînes de même longueur que 'segments'. "
                            "Un segment peut être un fragment de phrase séparé par une balise EPUB : "
                            "traduis-le sans ajouter d'information."
                        )},
                        {"role": "user", "content": payload},
                    ],
                )
                data = json.loads(response.choices[0].message.content or "")
                result = data.get("translations")
                if not isinstance(result, list) or len(result) != len(texts) or not all(isinstance(x, str) and x.strip() for x in result):
                    raise TranslationError("La traduction a renvoyé un nombre de segments incorrect.")
                return result
            except TranslationError:
                if attempt == 2:
                    raise
            except Exception as exc:
                if attempt == 2:
                    raise TranslationError(f"Erreur API de traduction : {exc}") from exc
            time.sleep(2 ** attempt)
        raise TranslationError("La traduction a échoué.")


def _translate_nodes(root, translator, progress, start, total):
    nodes = []
    for node in root.xpath("//*[local-name()='body']//text()"):
        if any(
            etree.QName(parent).localname in SKIP_TAGS
            for parent in [node.getparent(), *node.getparent().iterancestors()]
        ):
            continue
        value = str(node)
        if value.strip():
            nodes.append((node, value))
    done = start
    batch = []
    batch_chars = 0

    def flush():
        nonlocal done, batch, batch_chars
        if not batch:
            return
        translated = translator.translate([raw.strip() for _, raw in batch])
        for (node, raw), replacement in zip(batch, translated):
            prefix = re.match(r"^\s*", raw).group()
            suffix = re.search(r"\s*$", raw).group()
            value = prefix + replacement.strip() + suffix
            if node.is_tail:
                node.getparent().tail = value
            else:
                node.getparent().text = value
            done += len(raw.strip())
        progress(min(done, total), total)
        batch, batch_chars = [], 0

    for node, raw in nodes:
        if batch and (batch_chars + len(raw) > 3500 or len(batch) >= 35):
            flush()
        batch.append((node, raw))
        batch_chars += len(raw)
    flush()
    return done


def translate_epub(
    path: str | Path, destination: str | Path, api_key: str = "",
    progress=lambda done, total: None, checkpoint_dir: str | Path | None = None,
    translator=None,
) -> Path:
    source = Path(path)
    details = inspect_epub(source)
    translator = translator or OpenAITranslator(api_key)
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    checkpoints = Path(checkpoint_dir) if checkpoint_dir else None
    if checkpoints:
        checkpoints.mkdir(parents=True, exist_ok=True)
    book = epub.read_epub(str(source), {"ignore_ncx": True})
    total = details["characters"]
    done = 0
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        if isinstance(item, epub.EpubNav):
            continue
        original = item.get_content()
        count = sum(len(str(node).strip()) for node in etree.fromstring(original).xpath("//*[local-name()='body']//text()") if str(node).strip())
        marker = hashlib.sha256(item.file_name.encode()).hexdigest()[:16]
        saved = checkpoints / f"{marker}.xhtml" if checkpoints else None
        if saved and saved.is_file():
            try:
                cached = saved.read_bytes()
                etree.fromstring(cached)
                item.set_content(cached)
                done += count
                progress(min(done, total), total)
                continue
            except (OSError, etree.XMLSyntaxError):
                saved.unlink(missing_ok=True)
        root = etree.fromstring(original)
        done = _translate_nodes(root, translator, progress, done, total)
        content = etree.tostring(root, encoding="utf-8", xml_declaration=True)
        item.set_content(content)
        if saved:
            saved.write_bytes(content)

    # Translate the visible book title and navigation labels; hrefs and anchors
    # stay unchanged, so Books can still jump to the original sections.
    titles = book.get_metadata("DC", "title")
    if titles:
        translated_title = translator.translate([titles[0][0]])[0]
        book.metadata[DC]["title"] = [(translated_title, {})]
    book.metadata[DC]["language"] = [("fr", {})]

    toc_number = 0

    def translate_toc(entries):
        nonlocal toc_number
        for entry in entries:
            node, children = entry if isinstance(entry, tuple) else (entry, ())
            toc_number += 1
            if isinstance(node, epub.Link) and not node.uid:
                node.uid = f"translated-toc-{toc_number}"
            if getattr(node, "title", ""):
                node.title = translator.translate([node.title])[0]
            translate_toc(children)

    translate_toc(book.toc)
    temporary = output.with_suffix(".partial.epub")
    try:
        epub.write_epub(str(temporary), book)
        checked = epub.read_epub(str(temporary), {"ignore_ncx": True})
        if not any(checked.get_items_of_type(ebooklib.ITEM_DOCUMENT)):
            raise TranslationError("L’EPUB traduit ne contient aucun chapitre.")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
