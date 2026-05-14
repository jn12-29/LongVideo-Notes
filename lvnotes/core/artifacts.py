import re
from pathlib import Path

from lvnotes.core.cache import read_json_file
from lvnotes.core.exceptions import CacheError
from lvnotes.core.paths import PipelinePaths
from lvnotes.core.schemas import (
    AudioExtractResult,
    RefinedSegment,
    RefinedTranscript,
    SegmentList,
    Transcript,
    VisualAlignment,
    VisualDescriptionList,
    VisualSampleIndex,
    VisualSemanticJudgementList,
)
from lvnotes.core.transcript import slice_transcript_text

_REF_MARKER_RE = re.compile(r"\[\[REF:\d+\]\]")


class AudioArtifacts:
    def __init__(self, input_hash: str, paths: PipelinePaths) -> None:
        self.input_hash = input_hash
        self.paths = paths
        self._extract: AudioExtractResult | None = None
        self._transcript: Transcript | None = None
        self._segments: SegmentList | None = None
        self._refined: RefinedTranscript | None = None

    def get_extract(self) -> AudioExtractResult:
        if self._extract is None:
            self._extract = _read_required(self.paths.audio_extract_json, AudioExtractResult, "extract")
        return self._extract

    def get_transcript(self) -> Transcript:
        if self._transcript is None:
            self._transcript = _read_required(self.paths.transcript_raw_json, Transcript, "transcribe")
        return self._transcript

    def get_segments(self) -> SegmentList:
        if self._segments is None:
            self._segments = _read_required(self.paths.segments_json, SegmentList, "segment")
        return self._segments

    def get_refined(self) -> RefinedTranscript:
        if self._refined is None:
            self._refined = _read_required(self.paths.refined_transcript_json, RefinedTranscript, "refine")
        return self._refined

    def get_text_at(
        self,
        start: float,
        end: float,
        prefer_raw: bool = False,
        strict: bool = False,
        strip_refs: bool = True,
    ) -> str:
        if start >= end:
            raise ValueError("start must be less than end")
        duration = self.get_duration()
        if start < 0 or end > duration:
            raise ValueError("time range is outside audio duration")
        if prefer_raw:
            return slice_transcript_text(self.get_transcript(), start, end)
        else:
            pieces = [segment.cleaned_text for segment in self.get_refined().segments if _intersects(segment.start, segment.end, start, end, strict)]
            if strip_refs:
                pieces = [_REF_MARKER_RE.sub("", piece) for piece in pieces]
        return " ".join(piece.strip() for piece in pieces if piece.strip())

    def get_segment_at(self, timestamp: float) -> RefinedSegment | None:
        for segment in self.get_refined().segments:
            if segment.start <= timestamp <= segment.end:
                return segment
        return None

    def get_duration(self) -> float:
        if self.paths.refined_transcript_json.exists():
            return self.get_refined().duration
        if self.paths.transcript_raw_json.exists():
            return self.get_transcript().duration
        return self.get_extract().duration

    def get_language(self) -> str:
        if self.paths.refined_transcript_json.exists():
            return self.get_refined().language
        return self.get_transcript().language

    def is_complete(self) -> bool:
        return self.paths.refined_transcript_json.exists()


class VisualArtifacts:
    def __init__(self, input_hash: str, paths: PipelinePaths) -> None:
        self.input_hash = input_hash
        self.paths = paths
        self._filtered_samples: VisualSampleIndex | None = None
        self._semantic_samples: VisualSampleIndex | None = None
        self._semantic_judgements: VisualSemanticJudgementList | None = None
        self._alignments: list[VisualAlignment] | None = None
        self._descriptions: VisualDescriptionList | None = None

    def get_filtered_samples(self) -> VisualSampleIndex:
        if self._filtered_samples is None:
            self._filtered_samples = _read_required(self.paths.visual_filtered_sample_json, VisualSampleIndex, "visual_filter")
        return self._filtered_samples

    def get_semantic_samples(self) -> VisualSampleIndex:
        if self._semantic_samples is None:
            self._semantic_samples = _read_required(self.paths.visual_semantic_sample_json, VisualSampleIndex, "visual_semantic_filter")
        return self._semantic_samples

    def get_semantic_judgements(self) -> VisualSemanticJudgementList:
        if self._semantic_judgements is None:
            self._semantic_judgements = _read_required(self.paths.visual_semantic_judgements_json, VisualSemanticJudgementList, "visual_semantic_filter")
        return self._semantic_judgements

    def get_alignments(self) -> list[VisualAlignment]:
        if self._alignments is None:
            self._alignments = _read_required(self.paths.visual_alignments_json, list[VisualAlignment], "visual_align")  # type: ignore[assignment]
        return self._alignments

    def get_descriptions(self) -> VisualDescriptionList:
        if self._descriptions is None:
            self._descriptions = _read_required(self.paths.visual_descriptions_json, VisualDescriptionList, "visual_describe")
        return self._descriptions

    def is_complete(self) -> bool:
        return self.paths.visual_descriptions_json.exists()


def _read_required(path: Path, schema: type[object], stage_name: str):
    if not path.exists():
        raise CacheError(f"{path} not found; stage '{stage_name}' may not have run")
    return read_json_file(path, schema)


def _intersects(item_start: float, item_end: float, start: float, end: float, strict: bool) -> bool:
    if strict:
        return item_start >= start and item_end <= end
    return item_start < end and item_end > start
