import logging
import posixpath
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Tuple
from urllib.parse import unquote

import ebooklib
from bs4 import BeautifulSoup, NavigableString
from ebooklib import epub

from audiobook_generator.book_parsers.base_book_parser import BaseBookParser
from audiobook_generator.config.general_config import GeneralConfig

logger = logging.getLogger(__name__)
BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote"}
NON_NARRATED_TITLES = {"cover", "couverture", "contents", "sommaire", "table des matières", "index"}
SENTENCE_ENDINGS = (".", "!", "?", "…", ";", ":", ",")
DECORATIVE_LIST_PREFIX = re.compile(r"^[▶►▸◀✓✔•●▪]\s*")
DECORATIVE_INLINE_MARKER = re.compile(r"\s+[▶►▸◀✓✔•●▪]\s+")


@dataclass(frozen=True)
class TextBlock:
    file_name: str
    anchors: tuple[str, ...]
    text: str
    heading: bool
    list_item: bool = False


def _heading_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return re.sub(
        r"[^a-z0-9]+", "",
        "".join(char for char in normalized if not unicodedata.combining(char)),
    )


class EpubBookParser(BaseBookParser):
    def __init__(self, config: GeneralConfig):
        super().__init__(config)
        self.book = epub.read_epub(self.config.input_file, {"ignore_ncx": True})

    def validate_config(self):
        if self.config.input_file is None:
            raise ValueError("Epub Parser: Input file cannot be empty")
        if not self.config.input_file.endswith(".epub"):
            raise ValueError(f"Epub Parser: Unsupported file format: {self.config.input_file}")

    def get_book(self):
        return self.book

    def get_book_title(self) -> str:
        metadata = self.book.get_metadata("DC", "title")
        return metadata[0][0] if metadata else "Untitled"

    def get_book_author(self) -> str:
        metadata = self.book.get_metadata("DC", "creator")
        return metadata[0][0] if metadata else "Unknown"

    def _blocks(self) -> list[TextBlock]:
        blocks = []
        for item_id, linear in self.book.spine:
            item = self.book.get_item_with_id(item_id)
            if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT or linear == "no":
                continue
            soup = BeautifulSoup(item.get_content(), "lxml-xml")
            body = soup.find("body")
            if body is None:
                continue
            file_name = posixpath.normpath(item.get_name())
            for tag in body.find_all(BLOCK_TAGS):
                if tag.find_parent(BLOCK_TAGS) or tag.find_parent(["nav", "script", "style"]):
                    continue
                # Printed headers contain a page number plus a repeated part/chapter
                # title. Keep any prose that follows in the same PDF-derived line.
                if tag.find(lambda node: node.has_attr("id") and re.fullmatch(r"p\d+", node["id"])):
                    page_numbers = [node for node in tag.find_all("i", recursive=False)
                                    if re.fullmatch(r"\d{1,3}", node.get_text(strip=True))]
                    if page_numbers and tag.find("b", recursive=False):
                        for node in page_numbers + tag.find_all("b", recursive=False):
                            node.decompose()
                text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
                list_item = bool(DECORATIVE_LIST_PREFIX.match(text))
                if list_item:
                    text = DECORATIVE_LIST_PREFIX.sub("", text, count=1).strip()
                text = DECORATIVE_INLINE_MARKER.sub(" ", text)
                first_italic = tag.find(["i", "em"], recursive=False)
                if first_italic and re.fullmatch(r"[A-ZÀ-Ý]", first_italic.get_text(strip=True)):
                    text = re.sub(r"^([A-ZÀ-Ý]) ([a-zà-ÿ]+)", r"\1\2", text)
                # PDF-derived EPUBs often put the printed page number on its own line.
                if re.fullmatch(r"\d{1,3}", text):
                    text = ""
                anchors = tuple(
                    node.get("id") for node in [*tag.parents, tag, *tag.find_all(id=True)]
                    if node.get("id")
                )
                direct_text = "".join(str(node) for node in tag.children if isinstance(node, NavigableString)).strip()
                heading = tag.name.startswith("h") or (
                    not direct_text and tag.find(["b", "strong", "i", "em"]) is not None
                )
                blocks.append(TextBlock(file_name, anchors, text, heading, list_item))
            soup.decompose()
        return blocks

    def _navigation(self) -> list[tuple[str, str, str]]:
        entries = []

        def visit(nodes):
            for entry in nodes:
                node, children = entry if isinstance(entry, tuple) else (entry, ())
                title = re.sub(r"\s+", " ", node.title).strip()
                href = getattr(node, "href", "") or ""
                if title and href:
                    file_name, _, anchor = unquote(href).partition("#")
                    entries.append((title, posixpath.normpath(file_name), anchor))
                visit(children)

        visit(self.book.toc)
        return entries

    @staticmethod
    def _heading_position(blocks: list[TextBlock], title: str, start: int, end: int) -> int | None:
        target = _heading_key(title)
        best_score = 0.0
        best_position = None
        for position in range(start, end):
            if not blocks[position].heading or not blocks[position].text:
                continue
            position_score = 0.0
            for width in (1, 2, 3):
                candidate = _heading_key(" ".join(
                    block.text for block in blocks[position:position + width]
                ))[:max(len(target) + 10, 30)]
                if not candidate:
                    continue
                score = SequenceMatcher(None, target, candidate).ratio()
                if target in candidate:
                    score = max(score, 0.97)
                position_score = max(position_score, score)
                if score > best_score:
                    best_score, best_position = score, position
            # The navigation anchor is more trustworthy than a perfect match
            # in a repeated running header several pages later.
            if position_score >= 0.90:
                return position
        return best_position if best_score >= 0.70 else None

    def _clean_text(
        self, blocks: list[TextBlock], break_string: str, soft_break_string: str = ""
    ) -> str:
        pieces = []
        previous_text = ""
        for block in blocks:
            if not block.text:
                if self.config.newline_mode != "none" and pieces:
                    pieces.append(break_string)
                continue
            if (block.heading or block.list_item) and self.config.newline_mode != "none" and pieces:
                pieces.append(break_string)
            elif self.config.newline_mode == "single" and pieces:
                pieces.append(break_string)
            elif (
                soft_break_string and self.config.newline_mode != "none"
                and previous_text and not previous_text.endswith(SENTENCE_ENDINGS)
                and pieces and pieces[-1] != break_string
            ):
                pieces.append(soft_break_string)
            pieces.append(block.text)
            if (block.heading or block.list_item) and self.config.newline_mode != "none":
                pieces.append(break_string)
            previous_text = block.text
        text = " ".join(pieces)
        for marker in (break_string, soft_break_string):
            if marker:
                escaped = re.escape(marker)
                text = re.sub(r"\s*" + escaped + r"\s*", marker, text)
                text = re.sub(r"(?:" + escaped + r")+", marker, text)
        text = text.strip()
        for marker in (break_string, soft_break_string):
            if marker:
                text = text.removeprefix(marker).removesuffix(marker).strip()
        if self.config.remove_endnotes:
            text = re.sub(r'(?<=[a-zA-Z.,!?;”")])\d+', "", text)
        if self.config.remove_reference_numbers:
            text = re.sub(r'\[\d+(\.\d+)?\]', "", text)
        for replacement in self.get_search_and_replaces():
            text = re.sub(replacement["search"], replacement["replace"], text)
        return text

    def get_chapters(self, break_string, soft_break_string="") -> List[Tuple[str, str]]:
        blocks = self._blocks()
        if not blocks:
            return []
        positions = {}
        file_starts = {}
        for idx, block in enumerate(blocks):
            file_starts.setdefault(block.file_name, idx)
            for anchor in block.anchors:
                positions.setdefault((block.file_name, anchor), idx)

        markers = []
        for title, file_name, anchor in self._navigation():
            anchor_position = positions.get((file_name, anchor)) if anchor else file_starts.get(file_name)
            if anchor_position is None:
                continue
            start = max(anchor_position, markers[-1][0] + 1 if markers else 0)
            end = min(anchor_position + 80, len(blocks))
            position = self._heading_position(blocks, title, start, end) if start < end else None
            if position is None and anchor_position > (markers[-1][0] if markers else -1):
                position = anchor_position
            if position is not None:
                markers.append((position, title))
        if not markers:
            markers = sorted(
                (idx, blocks[idx].text[:60] or "Untitled") for idx in file_starts.values()
            )

        chapters = []
        for index, (start, title) in enumerate(markers):
            end = markers[index + 1][0] if index + 1 < len(markers) else len(blocks)
            if title.casefold() in NON_NARRATED_TITLES:
                continue
            text = self._clean_text(blocks[start:end], break_string, soft_break_string)
            if text.strip():
                chapters.append((title, text))
        return chapters

    def get_search_and_replaces(self):
        replacements = []
        if isinstance(self.config.search_and_replace_file, str) and self.config.search_and_replace_file:
            with open(self.config.search_and_replace_file) as fp:
                for line in fp:
                    if '==' in line and not line.startswith(('==', '#')):
                        search, replace = line.rstrip('\n').split('==', 1)
                        replacements.append({'search': search, 'replace': replace})
        return replacements

    @staticmethod
    def _sanitize_title(title, break_string) -> str:
        title = title.replace(break_string, " ")
        sanitized_title = re.sub(r"[^\w\s]", "", title, flags=re.UNICODE)
        return re.sub(r"\s+", "_", sanitized_title.strip())
