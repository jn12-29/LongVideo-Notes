from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SampledFrame:
    id: int
    timestamp: float
    image_source_path: Path


@dataclass(frozen=True)
class VisualSampleIndex:
    frames: list[SampledFrame]
    duration: float


@dataclass(frozen=True)
class VisualSegment:
    id: int
    start: float
    end: float
    frame_ids: list[int]


@dataclass(frozen=True)
class VisualSegmentList:
    segments: list[VisualSegment]


@dataclass(frozen=True)
class VisualJudgement:
    segment_id: int
    medium: str
    is_meaningful: bool
    evolution: str
    richest_frame_id: int | None


@dataclass(frozen=True)
class VisualJudgementList:
    judgements: list[VisualJudgement]


@dataclass(frozen=True)
class VisualSelection:
    segment_id: int
    frame_id: int
    start: float
    end: float
    image_source_path: Path
    medium: str


@dataclass(frozen=True)
class VisualDescription:
    segment_id: int
    frame_id: int
    start: float
    end: float
    image_source_path: Path
    medium: str
    description: str


@dataclass(frozen=True)
class VisualDescriptionList:
    descriptions: list[VisualDescription]
