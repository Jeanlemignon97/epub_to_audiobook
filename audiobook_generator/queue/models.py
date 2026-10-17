from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStatus(str, Enum):
    ANALYZING = "analyzing"
    PENDING = "pending"
    CONVERTING = "converting"
    PAUSED = "paused"
    FAILED = "failed"
    COMPLETED = "completed"


@dataclass(frozen=True)
class BookAnalysis:
    title: str
    author: str
    chapter_count: int
    total_characters: int
    estimated_hours: float
    estimated_cost: float


@dataclass(frozen=True)
class BookJob:
    id: str
    source_path: str
    output_dir: str
    original_name: str
    provider: str
    model: str
    voice: str
    analysis: BookAnalysis
    destination_dir: str = ""
    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0
    completed_chapters: int = 0
    error: str = ""
    final_path: str = ""
    max_cost: float | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
