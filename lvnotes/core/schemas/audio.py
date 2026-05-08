from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioExtractResult:
    audio_path: Path
    duration: float
    sample_rate: int
    channels: int
    source_path: Path
    source_codec: str
    source_sample_rate: int
    source_channels: int


@dataclass(frozen=True)
class WordTimestamp:
    word: str
    start: float
    end: float
    probability: float


@dataclass(frozen=True)
class TranscriptSegment:
    id: int
    start: float
    end: float
    text: str
    words: list[WordTimestamp]


@dataclass(frozen=True)
class Transcript:
    segments: list[TranscriptSegment]
    language: str
    duration: float


@dataclass(frozen=True)
class SegmentMarker:
    id: int
    start: float
    end: float
    topic_hint: str
    boundary_reason: str


@dataclass(frozen=True)
class SegmentList:
    markers: list[SegmentMarker]


@dataclass(frozen=True)
class RefinedSegment:
    id: int
    start: float
    end: float
    topic: str
    cleaned_text: str
    summary: str
    cross_refs: list[int]


@dataclass(frozen=True)
class RefinedTranscript:
    segments: list[RefinedSegment]
    language: str
    duration: float
