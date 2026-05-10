from dataclasses import dataclass
import logging
from pathlib import Path
import re

from jinja2 import Template

from lvnotes.core.cache import atomic_write_json, build_cache_key, hash_json, hash_prompt_template
from lvnotes.core.context import PipelineContext
from lvnotes.core.exceptions import CacheError, LLMError
from lvnotes.core.parallel import run_parallel
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.paths import resolve_visual_semantic_image_path
from lvnotes.core.schemas import RefinedSegment, VisualAlignment, VisualDescription, VisualDescriptionList
from lvnotes.llm import ImagePart, LLMMessage, LLMRequestOptions, TextPart, complete_json, for_task

from lvnotes.visual_pipeline._common import cache_output, cached_output, prompt_path, read_alignments

log = logging.getLogger(__name__)
_DESCRIPTION_CONTENT_RE = re.compile(r"[\w\u4e00-\u9fff]")


def run(ctx: PipelineContext) -> StageOutput:
    if not ctx.artifacts.audio.is_complete():
        raise CacheError("visual describe requires completed audio refine stage")
    alignments = read_alignments(ctx.paths.visual_alignments_json)
    refined = ctx.artifacts.audio.get_refined()
    segment_by_id = {segment.id: segment for segment in refined.segments}
    audio_texts = [_audio_text_for_alignment(ctx, segment_by_id[alignment.segment_id]) for alignment in alignments]
    template = prompt_path("describe.jinja")
    alignments_hash = hash_json(alignments)
    audio_hash = hash_json(audio_texts)
    prompt_hash = hash_prompt_template(template)
    profile_hash = hash_json(ctx.config.llm.profiles[ctx.config.tasks["slide_describe"]])
    cache_key = build_cache_key("visual_describe", {"alignments": alignments_hash, "audio_text": audio_hash, "profile": profile_hash, "prompt": prompt_hash})
    if not ctx.no_cache:
        cached = cached_output("visual_describe", [ctx.paths.visual_descriptions_json], cache_key)
        if cached is not None:
            return cached
    items = list(zip(alignments, audio_texts))
    descriptions = run_parallel(
        items,
        lambda item: _describe_one(ctx, template, segment_by_id, item),
        desc="visual.describe",
        unit="alignment",
        max_workers=ctx.config.visual_pipeline.describe.concurrent_calls,
    )
    result_list = VisualDescriptionList(descriptions=descriptions)
    atomic_write_json(ctx.paths.visual_descriptions_json, result_list)
    return cache_output("visual_describe", [ctx.paths.visual_descriptions_json], cache_key, {"alignments": alignments_hash, "audio_text": audio_hash}, "", prompt_hash, {"item_count": len(descriptions)})


@dataclass(frozen=True)
class _DescriptionOnly:
    description: str


def _describe_one(ctx: PipelineContext, template: Path, segment_by_id: dict[int, RefinedSegment], item: tuple[VisualAlignment, str]) -> VisualDescription:
    alignment, audio_text = item
    segment = segment_by_id[alignment.segment_id]
    image_path = resolve_visual_semantic_image_path(ctx.paths, alignment.image_source_path)
    base_prompt = Template(template.read_text(encoding="utf-8")).render(selection=alignment, audio_text=audio_text)
    result = None
    for attempt in range(3):
        prompt = base_prompt
        if attempt:
            prompt += "\n\nPrevious response was invalid because description had no meaningful text. Return a complete Simplified Chinese sentence in description."
        result = complete_json(
            for_task(ctx.config, "slide_describe"),
            [LLMMessage(role="user", content=[TextPart(text=prompt), ImagePart(path=image_path, mime_type="image/png")])],
            _DescriptionOnly,
            LLMRequestOptions(temperature=0.2),
            1,
        )
        if _has_description_content(result.description):
            break
    if result is None or not _has_description_content(result.description):
        raise LLMError(f"visual describe invariant failed: empty description frame_id={alignment.frame_id}")
    return VisualDescription(
        segment_id=alignment.segment_id,
        frame_id=alignment.frame_id,
        start=segment.start,
        end=segment.end,
        image_source_path=alignment.image_source_path,
        medium=alignment.medium,
        description=result.description,
    )


def _audio_text_for_alignment(ctx: PipelineContext, segment: RefinedSegment) -> str:
    return ctx.artifacts.audio.get_text_at(segment.start, segment.end, strip_refs=True)


def _has_description_content(description: str) -> bool:
    return _DESCRIPTION_CONTENT_RE.search(description) is not None
