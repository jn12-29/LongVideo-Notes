import logging
import re

from jinja2 import Template

from lvnotes.core.cache import atomic_write_json, build_cache_key, cache_manifest_path, hash_json, hash_prompt_template, read_cache_manifest, read_json_file
from lvnotes.core.context import PipelineContext
from lvnotes.core.exceptions import LLMError
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.schemas import RefinedSegment, RefinedTranscript, SegmentMarker, Transcript
from lvnotes.llm import LLMMessage, LLMRequestOptions, TextPart, complete_json, for_task

from lvnotes.audio_pipeline._common import cache_output, cached_output, prompt_path

log = logging.getLogger(__name__)
_REF_RE = re.compile(r"\[\[REF:(\d+)\]\]")


def run(ctx: PipelineContext) -> StageOutput:
    transcript = ctx.artifacts.audio.get_transcript()
    segments = ctx.artifacts.audio.get_segments()
    cfg = ctx.config.audio_pipeline.refine
    template = prompt_path("refine.jinja")
    transcript_hash = hash_json(transcript)
    segments_hash = hash_json(segments)
    config_hash = hash_json(cfg)
    prompt_hash = hash_prompt_template(template)
    profile_hash = hash_json(ctx.config.llm.profiles[ctx.config.tasks["refine"]])
    cache_key = build_cache_key("refine", {"transcript": transcript_hash, "segments": segments_hash, "config": config_hash, "profile": profile_hash, "prompt": prompt_hash})
    output_paths = [ctx.paths.refined_transcript_json]
    if not ctx.no_cache:
        cached = cached_output("refine", output_paths, cache_key)
        if cached is not None:
            log.info("audio.refine cache hit input_hash=%s", ctx.input_hash)
            return cached
    if ctx.no_cache or _manifest_cache_key_changed(ctx, cache_key):
        _clear_refined_dir(ctx)

    completed = _load_completed(ctx, len(segments.markers))
    for marker in segments.markers[len(completed) :]:
        refined = _refine_one(ctx, transcript, segments.markers, completed, marker)
        _validate_refined(refined, marker)
        atomic_write_json(ctx.paths.refined_dir / f"{refined.id:04d}.json", refined)
        completed.append(refined)
        if ctx.debug and refined.id == 0:
            print(f"refine debug first segment:\n{refined.cleaned_text[:1000]}")
            completed[0] = read_json_file(ctx.paths.refined_dir / "0000.json", RefinedSegment)  # type: ignore[assignment]

    result = RefinedTranscript(segments=completed, language=transcript.language, duration=transcript.duration)
    atomic_write_json(ctx.paths.refined_transcript_json, result)
    return cache_output("refine", output_paths, cache_key, {"transcript": transcript_hash, "segments": segments_hash}, config_hash, prompt_hash, {"item_count": len(completed)})


def _clear_refined_dir(ctx: PipelineContext) -> None:
    ctx.paths.refined_dir.mkdir(parents=True, exist_ok=True)
    for path in ctx.paths.refined_dir.glob("*.json"):
        path.unlink()


def _manifest_cache_key_changed(ctx: PipelineContext, cache_key: str) -> bool:
    manifest_path = cache_manifest_path(ctx.paths.refined_transcript_json)
    if not manifest_path.exists():
        return False
    return read_cache_manifest(manifest_path).cache_key != cache_key


def _load_completed(ctx: PipelineContext, expected_count: int) -> list[RefinedSegment]:
    completed: list[RefinedSegment] = []
    for segment_id in range(expected_count):
        path = ctx.paths.refined_dir / f"{segment_id:04d}.json"
        if not path.exists():
            break
        completed.append(read_json_file(path, RefinedSegment))  # type: ignore[arg-type]
    return completed


def _refine_one(ctx: PipelineContext, transcript: Transcript, markers: list[SegmentMarker], completed: list[RefinedSegment], marker: SegmentMarker) -> RefinedSegment:
    prompt = Template(prompt_path("refine.jinja").read_text(encoding="utf-8")).render(
        transcript="\n".join(f"[{item.start:.3f}-{item.end:.3f}] {item.text}" for item in transcript.segments),
        segments=markers,
        completed=completed,
        current=marker,
        current_text=" ".join(item.text for item in transcript.segments if item.start < marker.end and item.end > marker.start),
    )
    return complete_json(
        for_task(ctx.config, "refine"),
        [LLMMessage(role="system", content=[TextPart(text=prompt)])],
        RefinedSegment,
        LLMRequestOptions(temperature=0.2),
        max_repair_retries=1,
    )


def _validate_refined(refined: RefinedSegment, marker: SegmentMarker) -> None:
    if refined.id != marker.id or abs(refined.start - marker.start) > 0.2 or abs(refined.end - marker.end) > 0.2:
        raise LLMError("refine invariant failed: id or time mismatch")
    if any(ref >= refined.id for ref in refined.cross_refs):
        raise LLMError("refine invariant failed: future cross ref")
    marker_refs = {int(match.group(1)) for match in _REF_RE.finditer(refined.cleaned_text)}
    if marker_refs != set(refined.cross_refs):
        raise LLMError("refine invariant failed: cross refs do not match markers")
