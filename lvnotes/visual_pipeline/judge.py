import logging

from jinja2 import Template

from lvnotes.core.cache import atomic_write_json, build_cache_key, hash_json, hash_prompt_template
from lvnotes.core.context import PipelineContext
from lvnotes.core.exceptions import LLMError
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.schemas import VisualJudgementList
from lvnotes.llm import ImagePart, LLMMessage, LLMRequestOptions, TextPart, complete_json, for_task

from lvnotes.visual_pipeline._common import cache_output, cached_output, prompt_path, read_samples, read_segments

log = logging.getLogger(__name__)


def run(ctx: PipelineContext) -> StageOutput:
    segments = read_segments(ctx.paths.visual_segments_json)
    samples = read_samples(ctx.paths.visual_sample_json)
    frames = {frame.id: frame for frame in samples.frames}
    template = prompt_path("judge.jinja")
    segments_hash = hash_json(segments)
    prompt_hash = hash_prompt_template(template)
    profile_hash = hash_json(ctx.config.llm.profiles[ctx.config.tasks["slide_judge"]])
    cache_key = build_cache_key("visual_judge", {"segments": segments_hash, "profile": profile_hash, "prompt": prompt_hash})
    if not ctx.no_cache:
        cached = cached_output("visual_judge", [ctx.paths.visual_judgements_json], cache_key)
        if cached is not None:
            return cached
    prompt = Template(template.read_text(encoding="utf-8")).render(segments=segments.segments)
    candidates = {segment.id: _candidate_frame_ids(segment.frame_ids) for segment in segments.segments}
    parts = [TextPart(text=f"{prompt}\nCandidate frame ids by segment: {candidates}")]
    for segment in segments.segments:
        for frame_id in _candidate_frame_ids(segment.frame_ids):
            parts.append(ImagePart(path=ctx.paths.visual_frames_dir / frames[frame_id].image_source_path, mime_type="image/png"))
    judgements = complete_json(for_task(ctx.config, "slide_judge"), [LLMMessage(role="user", content=parts)], VisualJudgementList, LLMRequestOptions(temperature=0.1), 1)
    _validate_judgements(judgements, candidates)
    atomic_write_json(ctx.paths.visual_judgements_json, judgements)
    return cache_output("visual_judge", [ctx.paths.visual_judgements_json], cache_key, {"segments": segments_hash}, "", prompt_hash, {"item_count": len(judgements.judgements)})


def _validate_judgements(judgements: VisualJudgementList, candidates: dict[int, list[int]]) -> None:
    for judgement in judgements.judgements:
        candidate_ids = candidates.get(judgement.segment_id)
        if candidate_ids is None:
            raise LLMError("visual judge invariant failed")
        if judgement.richest_frame_id is not None and judgement.richest_frame_id not in candidate_ids:
            raise LLMError("visual judge invariant failed")


def _candidate_frame_ids(frame_ids: list[int]) -> list[int]:
    if len(frame_ids) <= 3:
        return frame_ids
    return [frame_ids[0], frame_ids[len(frame_ids) // 2], frame_ids[-1]]
