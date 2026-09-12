from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressEvent:
    chapter_index: int
    chapter_count: int | None
    state: str
    path: str = ""
