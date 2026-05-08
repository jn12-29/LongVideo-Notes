from dataclasses import dataclass
from pathlib import Path

from lvnotes.core.schemas.audio import RefinedSegment


@dataclass(frozen=True)
class VisualSlot:
    image_source_path: Path
    description: str
    medium: str
    start: float
    end: float
    visual_segment_id: int


@dataclass(frozen=True)
class ContentBlock:
    id: int
    start: float
    end: float
    topic: str
    cleaned_text: str
    summary: str
    cross_refs: list[int]
    visuals: list[VisualSlot]

    @classmethod
    def from_refined(cls, segment: RefinedSegment, visuals: list[VisualSlot]) -> "ContentBlock":
        return cls(
            id=segment.id,
            start=segment.start,
            end=segment.end,
            topic=segment.topic,
            cleaned_text=segment.cleaned_text,
            summary=segment.summary,
            cross_refs=segment.cross_refs,
            visuals=visuals,
        )


@dataclass(frozen=True)
class Chapter:
    id: int
    title: str
    summary: str
    block_id_start: int
    block_id_end: int


@dataclass(frozen=True)
class Outline:
    chapters: list[Chapter]
