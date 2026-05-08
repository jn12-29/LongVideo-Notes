import logging

from jinja2 import Template

from lvnotes.core.cache import atomic_write_json, build_cache_key, hash_file, hash_json, hash_prompt_template
from lvnotes.core.context import PipelineContext
from lvnotes.core.exceptions import LLMError
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.schemas import Outline
from lvnotes.llm import LLMMessage, LLMRequestOptions, TextPart, complete_json, for_task

from lvnotes.merge._common import cache_output, cached_output, prompt_path, read_blocks

log = logging.getLogger(__name__)


def run(ctx: PipelineContext) -> StageOutput:
    blocks = read_blocks(ctx.paths.content_blocks_json)
    cfg = ctx.config.merge.outline
    template = prompt_path("outline.jinja")
    blocks_hash = hash_file(ctx.paths.content_blocks_json)
    config_hash = hash_json(cfg)
    prompt_hash = hash_prompt_template(template)
    profile_hash = hash_json(ctx.config.llm.profiles[ctx.config.tasks["outline"]])
    cache_key = build_cache_key("outline", {"blocks": blocks_hash, "config": config_hash, "profile": profile_hash, "prompt": prompt_hash})
    output_paths = [ctx.paths.outline_json]
    if not ctx.no_cache:
        cached = cached_output("outline", output_paths, cache_key)
        if cached is not None:
            log.info("merge.outline cache hit input_hash=%s", ctx.input_hash)
            return cached

    prompt = Template(template.read_text(encoding="utf-8")).render(blocks=blocks, target_chapter_count_hint=cfg.target_chapter_count_hint)
    outline = complete_json(for_task(ctx.config, "outline"), [LLMMessage(role="user", content=[TextPart(text=prompt)])], Outline, LLMRequestOptions(temperature=0.2), 1)
    _validate_outline(outline, len(blocks))
    atomic_write_json(ctx.paths.outline_json, outline)
    return cache_output("outline", output_paths, cache_key, {"blocks": blocks_hash}, config_hash, prompt_hash, {"item_count": len(outline.chapters)})


def _validate_outline(outline: Outline, block_count: int) -> None:
    chapters = outline.chapters
    if not chapters:
        raise LLMError("outline invariant failed: no chapters")
    if chapters[0].block_id_start != 0 or chapters[-1].block_id_end != block_count - 1:
        raise LLMError("outline invariant failed: coverage")
    for expected_id, chapter in enumerate(chapters, start=1):
        if chapter.id != expected_id or chapter.block_id_start > chapter.block_id_end:
            raise LLMError("outline invariant failed: id or range")
    for left, right in zip(chapters, chapters[1:]):
        if left.block_id_end + 1 != right.block_id_start:
            raise LLMError("outline invariant failed: contiguous ranges")
