import logging

from jinja2 import Template

from lvnotes.core.cache import atomic_write_json, build_cache_key, hash_json, hash_prompt_template
from lvnotes.core.context import PipelineContext
from lvnotes.core.exceptions import CacheError, LLMError
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.paths import resolve_visual_image_path
from lvnotes.core.schemas import VisualDescription, VisualDescriptionList
from lvnotes.llm import ImagePart, LLMMessage, LLMRequestOptions, TextPart, complete_json, for_task

from lvnotes.visual_pipeline._common import cache_output, cached_output, prompt_path, read_selections

log = logging.getLogger(__name__)


def run(ctx: PipelineContext) -> StageOutput:
    if not ctx.artifacts.audio.is_complete():
        raise CacheError("visual describe requires completed audio refine stage")
    selections = read_selections(ctx.paths.visual_selections_json)
    audio_texts = [ctx.artifacts.audio.get_text_at(selection.start, selection.end, strip_refs=True) for selection in selections]
    template = prompt_path("describe.jinja")
    selections_hash = hash_json(selections)
    audio_hash = hash_json(audio_texts)
    prompt_hash = hash_prompt_template(template)
    profile_hash = hash_json(ctx.config.llm.profiles[ctx.config.tasks["slide_describe"]])
    cache_key = build_cache_key("visual_describe", {"selections": selections_hash, "audio_text": audio_hash, "profile": profile_hash, "prompt": prompt_hash})
    if not ctx.no_cache:
        cached = cached_output("visual_describe", [ctx.paths.visual_descriptions_json], cache_key)
        if cached is not None:
            return cached
    descriptions = []
    for selection, audio_text in zip(selections, audio_texts):
        image_path = resolve_visual_image_path(ctx.paths, selection.image_source_path)
        prompt = Template(template.read_text(encoding="utf-8")).render(selection=selection, audio_text=audio_text)
        result = complete_json(for_task(ctx.config, "slide_describe"), [LLMMessage(role="user", content=[TextPart(text=prompt), ImagePart(path=image_path, mime_type="image/png")])], VisualDescription, LLMRequestOptions(temperature=0.2), 1)
        if result.segment_id != selection.segment_id or result.image_source_path != selection.image_source_path:
            raise LLMError("visual describe invariant failed")
        descriptions.append(result)
    result_list = VisualDescriptionList(descriptions=descriptions)
    atomic_write_json(ctx.paths.visual_descriptions_json, result_list)
    return cache_output("visual_describe", [ctx.paths.visual_descriptions_json], cache_key, {"selections": selections_hash, "audio_text": audio_hash}, "", prompt_hash, {"item_count": len(descriptions)})
