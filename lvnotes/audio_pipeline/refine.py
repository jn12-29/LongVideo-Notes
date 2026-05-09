import logging
import re

from jinja2 import Template

from lvnotes.core.cache import (
    atomic_write_json,
    atomic_write_text,
    build_cache_key,
    cache_manifest_path,
    hash_json,
    hash_prompt_template,
    read_cache_manifest,
    read_json_file,
)
from lvnotes.core.context import PipelineContext
from lvnotes.core.exceptions import LLMError
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.progress import progress_bar, progress_write
from lvnotes.core.schemas import (
    RefinedSegment,
    RefinedSegmentList,
    RefinedTranscript,
    SegmentList,
    SegmentMarker,
    Transcript,
)
from lvnotes.core.transcript import slice_transcript_text
from lvnotes.llm import LLMMessage, LLMRequestOptions, TextPart, complete_json, for_task

from lvnotes.audio_pipeline._common import cache_output, cached_output, prompt_path

log = logging.getLogger(__name__)
_REF_RE = re.compile(r"\[\[REF:(\d+)\]\]")


def run(ctx: PipelineContext) -> StageOutput:
    transcript = ctx.artifacts.audio.get_transcript()
    segments = ctx.artifacts.audio.get_segments()
    cfg = ctx.config.audio_pipeline.refine
    prompt_hash = hash_json(
        {
            "serial": hash_prompt_template(prompt_path("refine.jinja")),
            "single": hash_prompt_template(prompt_path("refine_single.jinja")),
            "batch": hash_prompt_template(prompt_path("refine_batch.jinja")),
        }
    )
    transcript_hash = hash_json(transcript)
    segments_hash = hash_json(segments)
    config_hash = hash_json(cfg)
    profile_hash = hash_json(ctx.config.llm.profiles[ctx.config.tasks["refine"]])
    cache_key = build_cache_key(
        "refine",
        {"transcript": transcript_hash, "segments": segments_hash, "config": config_hash, "profile": profile_hash, "prompt": prompt_hash},
    )
    output_paths = [ctx.paths.refined_transcript_json]
    if not ctx.no_cache:
        cached = cached_output("refine", output_paths, cache_key)
        if cached is not None and _cached_refined_outputs_valid(ctx, transcript, segments.markers):
            log.info("audio.refine cache hit input_hash=%s", ctx.input_hash)
            return cached
    if ctx.no_cache or _partial_cache_key_changed(ctx, cache_key):
        _clear_refined_dir(ctx)
    _write_partial_cache_key(ctx, cache_key)

    result = _run_refine(ctx, transcript, segments)
    _validate_refined_transcript(result, transcript, segments.markers)
    _write_refined_outputs(ctx, result)
    return cache_output(
        "refine",
        output_paths,
        cache_key,
        {"transcript": transcript_hash, "segments": segments_hash},
        config_hash,
        prompt_hash,
        {"item_count": len(result.segments), "mode": cfg.mode},
    )


def _run_refine(ctx: PipelineContext, transcript: Transcript, segments: SegmentList) -> RefinedTranscript:
    mode = ctx.config.audio_pipeline.refine.mode
    with progress_bar(desc="audio.refine", total=len(segments.markers), unit="segment") as bar:
        if mode == "single_call":
            result = _run_single_call(ctx, transcript, segments)
            bar.update(len(segments.markers) - bar.n)
            return result
        if mode == "batched":
            return _run_batched(ctx, transcript, segments, progress=bar)
        if mode == "serial":
            return _run_serial(ctx, transcript, segments.markers, progress=bar)
        if mode == "adaptive":
            return _run_adaptive(ctx, transcript, segments, progress=bar)
    raise AssertionError(f"unknown refine mode: {mode}")


def _run_adaptive(
    ctx: PipelineContext,
    transcript: Transcript,
    segments: SegmentList,
    progress=None,
) -> RefinedTranscript:
    try:
        result = _run_single_call(ctx, transcript, segments)
        if progress is not None:
            progress.update(len(segments.markers) - progress.n)
        return result
    except Exception as exc:
        log.warning("refine single_call failed; falling back to batched: %s", exc)
        progress_write(f"audio.refine: single_call failed; falling back to batched: {exc}")
    return _run_batched(ctx, transcript, segments, fallback_serial=True, progress=progress)


def _run_single_call(ctx: PipelineContext, transcript: Transcript, segments: SegmentList) -> RefinedTranscript:
    prompt = _render_prompt(
        "refine_single.jinja",
        transcript=transcript,
        segments=segments.markers,
        segment_inputs=_segment_inputs(transcript, segments.markers),
        language=transcript.language,
        duration=transcript.duration,
    )
    result = complete_json(
        for_task(ctx.config, "refine"),
        [LLMMessage(role="system", content=[TextPart(text=prompt)])],
        RefinedTranscript,
        LLMRequestOptions(temperature=0.2),
        max_repair_retries=1,
    )
    _validate_refined_transcript(result, transcript, segments.markers)
    return result


def _run_batched(
    ctx: PipelineContext,
    transcript: Transcript,
    segments: SegmentList,
    fallback_serial: bool = False,
    progress=None,
) -> RefinedTranscript:
    refined: list[RefinedSegment] = []
    batch_size = ctx.config.audio_pipeline.refine.batch_size
    for start in range(0, len(segments.markers), batch_size):
        batch_markers = segments.markers[start : start + batch_size]
        progress_updated = False
        try:
            batch = _refine_batch(ctx, transcript, segments.markers, batch_markers)
            _validate_refined_segments(batch, batch_markers)
        except Exception as exc:
            if not fallback_serial:
                raise
            log.warning(
                "refine batch %s-%s failed; falling back to serial: %s",
                batch_markers[0].id,
                batch_markers[-1].id,
                exc,
            )
            progress_write(
                f"audio.refine: batch {batch_markers[0].id}-{batch_markers[-1].id} "
                f"failed; falling back to serial: {exc}"
            )
            batch = _run_serial_segments(ctx, transcript, segments.markers, batch_markers, progress=progress)
            progress_updated = True
        refined.extend(batch)
        _write_refined_segments(ctx, batch)
        if progress is not None and not progress_updated:
            progress.update(len(batch))
    result = RefinedTranscript(segments=refined, language=transcript.language, duration=transcript.duration)
    _validate_refined_transcript(result, transcript, segments.markers)
    return result


def _refine_batch(ctx: PipelineContext, transcript: Transcript, all_markers: list[SegmentMarker], batch_markers: list[SegmentMarker]) -> list[RefinedSegment]:
    prompt = _render_prompt(
        "refine_batch.jinja",
        transcript=transcript,
        all_segments=all_markers,
        batch_segments=batch_markers,
        segment_inputs=_segment_inputs(transcript, batch_markers),
    )
    result = complete_json(
        for_task(ctx.config, "refine"),
        [LLMMessage(role="system", content=[TextPart(text=prompt)])],
        RefinedSegmentList,
        LLMRequestOptions(temperature=0.2),
        max_repair_retries=1,
    )
    return result.segments


def _run_serial(ctx: PipelineContext, transcript: Transcript, markers: list[SegmentMarker], progress=None) -> RefinedTranscript:
    completed = _load_valid_completed(ctx, markers)
    if progress is not None and completed:
        progress.update(len(completed))
    refined = completed + _run_serial_segments(ctx, transcript, markers, markers[len(completed) :], progress=progress)
    result = RefinedTranscript(segments=refined, language=transcript.language, duration=transcript.duration)
    _validate_refined_transcript(result, transcript, markers)
    return result


def _run_serial_segments(
    ctx: PipelineContext,
    transcript: Transcript,
    all_markers: list[SegmentMarker],
    markers: list[SegmentMarker],
    progress=None,
) -> list[RefinedSegment]:
    refined: list[RefinedSegment] = []
    for marker in markers:
        segment = _refine_one(ctx, transcript, all_markers, marker)
        _validate_refined(segment, marker)
        atomic_write_json(ctx.paths.refined_dir / f"{segment.id:04d}.json", segment)
        refined.append(segment)
        if progress is not None:
            progress.update(1)
        if ctx.debug and segment.id == 0:
            progress_write(f"refine debug first segment:\n{segment.cleaned_text[:1000]}")
            refined[0] = read_json_file(ctx.paths.refined_dir / "0000.json", RefinedSegment)  # type: ignore[assignment]
    return refined


def _refine_one(ctx: PipelineContext, transcript: Transcript, markers: list[SegmentMarker], marker: SegmentMarker) -> RefinedSegment:
    prompt = _render_prompt(
        "refine.jinja",
        transcript=transcript,
        segments=markers,
        current=marker,
        current_text=slice_transcript_text(transcript, marker.start, marker.end),
    )
    return complete_json(
        for_task(ctx.config, "refine"),
        [LLMMessage(role="system", content=[TextPart(text=prompt)])],
        RefinedSegment,
        LLMRequestOptions(temperature=0.2),
        max_repair_retries=1,
    )


def _render_prompt(template_name: str, **values: object) -> str:
    return Template(prompt_path(template_name).read_text(encoding="utf-8")).render(**values, transcript_lines=_transcript_lines(values.get("transcript")))


def _transcript_lines(transcript: object) -> str:
    if not isinstance(transcript, Transcript):
        return ""
    return "\n".join(f"[{item.start:.3f}-{item.end:.3f}] {item.text}" for item in transcript.segments)


def _segment_inputs(transcript: Transcript, markers: list[SegmentMarker]) -> list[dict[str, object]]:
    return [
        {
            "id": marker.id,
            "start": marker.start,
            "end": marker.end,
            "topic_hint": marker.topic_hint,
            "text": slice_transcript_text(transcript, marker.start, marker.end),
        }
        for marker in markers
    ]


def _write_refined_outputs(ctx: PipelineContext, result: RefinedTranscript) -> None:
    _write_refined_segments(ctx, result.segments)
    atomic_write_json(ctx.paths.refined_transcript_json, result)


def _write_refined_segments(ctx: PipelineContext, segments: list[RefinedSegment]) -> None:
    for segment in segments:
        atomic_write_json(ctx.paths.refined_dir / f"{segment.id:04d}.json", segment)


def _clear_refined_dir(ctx: PipelineContext) -> None:
    ctx.paths.refined_dir.mkdir(parents=True, exist_ok=True)
    for path in ctx.paths.refined_dir.glob("*.json"):
        path.unlink()


def _partial_cache_key_changed(ctx: PipelineContext, cache_key: str) -> bool:
    key_path = ctx.paths.refined_dir / ".cache_key"
    if key_path.exists():
        return key_path.read_text(encoding="utf-8").strip() != cache_key
    manifest_path = cache_manifest_path(ctx.paths.refined_transcript_json)
    if not manifest_path.exists():
        return any(ctx.paths.refined_dir.glob("*.json"))
    return read_cache_manifest(manifest_path).cache_key != cache_key


def _write_partial_cache_key(ctx: PipelineContext, cache_key: str) -> None:
    atomic_write_text(ctx.paths.refined_dir / ".cache_key", cache_key + "\n")


def _load_completed(ctx: PipelineContext, expected_count: int) -> list[RefinedSegment]:
    completed: list[RefinedSegment] = []
    for segment_id in range(expected_count):
        path = ctx.paths.refined_dir / f"{segment_id:04d}.json"
        if not path.exists():
            break
        completed.append(read_json_file(path, RefinedSegment))  # type: ignore[arg-type]
    return completed


def _load_valid_completed(ctx: PipelineContext, markers: list[SegmentMarker]) -> list[RefinedSegment]:
    valid: list[RefinedSegment] = []
    for marker in markers:
        path = ctx.paths.refined_dir / f"{marker.id:04d}.json"
        if not path.exists():
            break
        try:
            segment = read_json_file(path, RefinedSegment)  # type: ignore[arg-type]
            _validate_refined(segment, marker)
        except Exception:
            break
        valid.append(segment)
    return valid


def _refined_segment_files_complete(ctx: PipelineContext, markers: list[SegmentMarker]) -> bool:
    expected_names = {f"{marker.id:04d}.json" for marker in markers}
    actual_names = {path.name for path in ctx.paths.refined_dir.glob("*.json")}
    if actual_names != expected_names:
        return False
    return len(_load_valid_completed(ctx, markers)) == len(markers)


def _cached_refined_outputs_valid(ctx: PipelineContext, transcript: Transcript, markers: list[SegmentMarker]) -> bool:
    if not _refined_segment_files_complete(ctx, markers):
        return False
    try:
        result = read_json_file(ctx.paths.refined_transcript_json, RefinedTranscript)  # type: ignore[arg-type]
        _validate_refined_transcript(result, transcript, markers)
    except Exception:
        return False
    return True


def _validate_refined_transcript(result: RefinedTranscript, transcript: Transcript, markers: list[SegmentMarker]) -> None:
    if result.language != transcript.language or abs(result.duration - transcript.duration) > 0.2:
        raise LLMError("refine invariant failed: transcript metadata mismatch")
    _validate_refined_segments(result.segments, markers)


def _validate_refined_segments(refined: list[RefinedSegment], markers: list[SegmentMarker]) -> None:
    if len(refined) != len(markers):
        raise LLMError("refine invariant failed: segment count mismatch")
    for segment, marker in zip(refined, markers):
        _validate_refined(segment, marker)


def _validate_refined(refined: RefinedSegment, marker: SegmentMarker) -> None:
    if refined.id != marker.id or abs(refined.start - marker.start) > 0.2 or abs(refined.end - marker.end) > 0.2:
        raise LLMError("refine invariant failed: id or time mismatch")
    if not refined.topic.strip() or not refined.cleaned_text.strip() or not refined.summary.strip():
        raise LLMError("refine invariant failed: empty text field")
    if any(ref >= refined.id or ref < 0 for ref in refined.cross_refs):
        raise LLMError("refine invariant failed: invalid cross ref")
    marker_refs = {int(match.group(1)) for match in _REF_RE.finditer(refined.cleaned_text)}
    if marker_refs != set(refined.cross_refs):
        raise LLMError("refine invariant failed: cross refs do not match markers")
