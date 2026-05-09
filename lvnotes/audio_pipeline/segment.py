import logging
from bisect import bisect_left

from jinja2 import Template

from lvnotes.core.cache import atomic_write_json, build_cache_key, hash_json, hash_prompt_template
from lvnotes.core.context import PipelineContext
from lvnotes.core.exceptions import LLMError
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.schemas import SegmentList, SegmentMarker, Transcript
from lvnotes.llm import LLMMessage, LLMRequestOptions, TextPart, complete_json, for_task

from lvnotes.audio_pipeline._common import cache_output, cached_output, prompt_path

log = logging.getLogger(__name__)
TRANSCRIPT_RENDER_VERSION = "word_timestamps_snap_segment_edges_v1"
SNAP_WARNING_THRESHOLD_SECONDS = 0.2
SNAP_FAILURE_THRESHOLD_SECONDS = 2.0


def run(ctx: PipelineContext) -> StageOutput:
    transcript = ctx.artifacts.audio.get_transcript()
    cfg = ctx.config.audio_pipeline.segment
    template = prompt_path("segment.jinja")
    transcript_hash = hash_json(transcript)
    config_hash = hash_json(cfg)
    prompt_hash = hash_prompt_template(template)
    profile_hash = hash_json(ctx.config.llm.profiles[ctx.config.tasks["segment"]])
    cache_key = build_cache_key("segment", {"transcript": transcript_hash, "config": config_hash, "profile": profile_hash, "prompt": prompt_hash, "render": TRANSCRIPT_RENDER_VERSION})
    output_paths = [ctx.paths.segments_json]
    if not ctx.no_cache:
        cached = cached_output("segment", output_paths, cache_key)
        if cached is not None:
            log.info("audio.segment cache hit input_hash=%s", ctx.input_hash)
            return cached

    prompt = _render_prompt(template, transcript)
    segments = complete_json(
        for_task(ctx.config, "segment"),
        [LLMMessage(role="user", content=[TextPart(text=prompt)])],
        SegmentList,
        LLMRequestOptions(temperature=0.2),
        max_repair_retries=1,
    )
    segments = _snap_segments_to_transcript_timestamps(segments, transcript)
    _validate_segments(segments, transcript.duration)
    atomic_write_json(ctx.paths.segments_json, segments)
    return cache_output("segment", output_paths, cache_key, {"transcript": transcript_hash}, config_hash, prompt_hash, {"item_count": len(segments.markers)})


def _render_prompt(template_path, transcript: Transcript) -> str:
    transcript_lines = _transcript_lines(transcript)
    return Template(template_path.read_text(encoding="utf-8")).render(
        duration=transcript.duration,
        transcript="\n".join(transcript_lines),
    )


def _transcript_lines(transcript: Transcript) -> list[str]:
    lines: list[str] = []
    for segment in transcript.segments:
        if not segment.words:
            lines.append(f"[{segment.start:.3f}-{segment.end:.3f}] {segment.text}")
            continue
        for word in segment.words:
            text = word.word.strip()
            if text:
                lines.append(f"[{word.start:.3f}-{word.end:.3f}] {text}")
    return lines


def _validate_segments(segments: SegmentList, duration: float) -> None:
    markers = segments.markers
    if not markers:
        raise LLMError("segment invariant failed: no markers")
    for expected_id, marker in enumerate(markers):
        if marker.id != expected_id or marker.start >= marker.end:
            raise LLMError("segment invariant failed: id order or time range")
    for left, right in zip(markers, markers[1:]):
        if left.end > right.start:
            raise LLMError("segment invariant failed: overlapping markers")
    if markers[0].start < 0.0 or markers[-1].end > duration:
        raise LLMError("segment invariant failed: outside transcript duration")


def _snap_segments_to_transcript_timestamps(segments: SegmentList, transcript: Transcript) -> SegmentList:
    markers = segments.markers
    if not markers:
        return segments
    candidates = _boundary_candidates(transcript)
    snapped_markers: list[SegmentMarker] = []
    for marker in markers:
        snapped_markers.append(
            SegmentMarker(
                id=marker.id,
                start=_snap_boundary(marker.start, candidates, f"segment {marker.id} start"),
                end=_snap_boundary(marker.end, candidates, f"segment {marker.id} end"),
                topic_hint=marker.topic_hint,
                boundary_reason=marker.boundary_reason,
            )
        )
    return SegmentList(markers=snapped_markers)


def _boundary_candidates(transcript: Transcript) -> list[float]:
    candidates = set()
    for segment in transcript.segments:
        if segment.words:
            for word in segment.words:
                candidates.add(word.start)
                candidates.add(word.end)
        else:
            candidates.add(segment.start)
            candidates.add(segment.end)
    return sorted(candidates)


def _snap_boundary(boundary: float, candidates: list[float], label: str) -> float:
    snapped, distance = _nearest_boundary(boundary, candidates)
    _warn_or_fail_distance(distance, label, boundary, snapped)
    return snapped


def _nearest_boundary(boundary: float, candidates: list[float]) -> tuple[float, float]:
    if not candidates:
        raise LLMError("segment snap failed: no transcript boundary candidates")
    index = bisect_left(candidates, boundary)
    nearby = []
    if index < len(candidates):
        nearby.append(candidates[index])
    if index > 0:
        nearby.append(candidates[index - 1])
    snapped = min(nearby, key=lambda candidate: abs(candidate - boundary))
    return snapped, abs(snapped - boundary)


def _warn_or_fail_distance(distance: float, label: str, boundary: float, snapped: float) -> None:
    if distance > SNAP_FAILURE_THRESHOLD_SECONDS:
        raise LLMError(f"segment boundary {label} is {distance:.3f}s from nearest transcript timestamp")
    if distance > SNAP_WARNING_THRESHOLD_SECONDS:
        log.warning(
            "segment boundary %s snapped from %.3f to %.3f (distance %.3fs)",
            label,
            boundary,
            snapped,
            distance,
        )
