from __future__ import annotations

import re
import unicodedata


def safe_book_name(title: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title.replace("’", "_"))
    ascii_title = ascii_title.encode("ascii", "ignore").decode()
    safe = re.sub(r"[^A-Za-z0-9]+", "_", ascii_title).strip("_")
    return safe[:100] or "livre_audio"
