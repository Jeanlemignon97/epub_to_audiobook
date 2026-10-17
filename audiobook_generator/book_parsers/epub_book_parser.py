import logging
import posixpath
import re
from dataclasses import dataclass
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


@dataclass(frozen=True)
class TextBlock:
    file_name: str
    anchors: tuple[str, ...]
    text: str
    heading: bool


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
                blocks.append(TextBlock(file_name, anchors, text, heading))
            soup.decompose()
        return blocks

    def _navigation(self) -> list[tuple[str, str, str]]:
        entries = []

        def visit(nodes, depth=0):
            for entry in nodes:
                node, children = entry if isinstance(entry, tuple) else (entry, ())
                if depth <= 1:
                    title = re.sub(r"\s+", " ", node.title).strip()
                    href = getattr(node, "href", "") or ""
                    if title and href:
                        file_name, _, anchor = unquote(href).partition("#")
                        entries.append((title, posixpath.normpath(file_name), anchor))
                visit(children, depth + 1)

        visit(self.book.toc)
        return entries

    def _clean_text(self, blocks: list[TextBlock], break_string: str) -> str:
        pieces = []
        for block in blocks:
            if not block.text:
                if self.config.newline_mode != "none" and pieces:
                    pieces.append(break_string)
                continue
            if block.heading and self.config.newline_mode != "none" and pieces:
                pieces.append(break_string)
            elif self.config.newline_mode == "single" and pieces:
                pieces.append(break_string)
            pieces.append(block.text)
            if block.heading and self.config.newline_mode != "none":
                pieces.append(break_string)
        text = " ".join(pieces)
        if break_string:
            escaped = re.escape(break_string)
            text = re.sub(r"\s*" + escaped + r"\s*", break_string, text)
            text = re.sub(r"(?:" + escaped + r")+", break_string, text)
            text = text.strip().strip(break_string).strip()
        if self.config.remove_endnotes:
            text = re.sub(r'(?<=[a-zA-Z.,!?;”")])\d+', "", text)
        if self.config.remove_reference_numbers:
            text = re.sub(r'\[\d+(\.\d+)?\]', "", text)
        for replacement in self.get_search_and_replaces():
            text = re.sub(replacement["search"], replacement["replace"], text)
        return text

    def get_chapters(self, break_string) -> List[Tuple[str, str]]:
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
            position = positions.get((file_name, anchor)) if anchor else file_starts.get(file_name)
            if position is not None and (not markers or markers[-1][0] != position):
                markers.append((position, title))
        markers.sort(key=lambda marker: marker[0])
        if not markers:
            markers = sorted(
                (idx, blocks[idx].text[:60] or "Untitled") for idx in file_starts.values()
            )

        chapters = []
        for index, (start, title) in enumerate(markers):
            end = markers[index + 1][0] if index + 1 < len(markers) else len(blocks)
            if title.casefold() in NON_NARRATED_TITLES:
                continue
            text = self._clean_text(blocks[start:end], break_string)
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
