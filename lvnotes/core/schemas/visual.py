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
class VisualSemanticJudgement:
    frame_id: int
    medium: str
    is_meaningful: bool
    reason: str


@dataclass(frozen=True)
class VisualSemanticJudgementList:
    judgements: list[VisualSemanticJudgement]


@dataclass(frozen=True)
class VisualAlignment:
    segment_id: int
    frame_id: int
    timestamp: float
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
