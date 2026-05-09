import logging
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Template

from lvnotes.core.cache import atomic_write_json, build_cache_key, hash_file, hash_json, hash_prompt_template
from lvnotes.core.context import PipelineContext
from lvnotes.core.exceptions import LLMError
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.serialization import to_jsonable
from lvnotes.core.schemas import Outline
from lvnotes.llm import LLMMessage, LLMRequestOptions, TextPart, complete_json_with_raw, for_task

from lvnotes.merge._common import cache_output, cached_output, prompt_path, read_blocks

log = logging.getLogger(__name__)
_OUTLINE_INVARIANT_RETRIES = 1


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
    client = for_task(ctx.config, "outline")
    messages = [LLMMessage(role="user", content=[TextPart(text=prompt)])]
    outline = None
    for attempt_index in range(_OUTLINE_INVARIANT_RETRIES + 1):
        outline, result = complete_json_with_raw(client, messages, Outline, LLMRequestOptions(temperature=0.2), 1)
        raw_text = result.text
        try:
            _validate_outline(outline, len(blocks))
            break
        except LLMError as exc:
            debug_path = _write_outline_failure_debug(ctx, exc, outline, raw_text, prompt, len(blocks), attempt_index + 1)
            log.warning("merge.outline invariant failed; wrote debug artifact: %s", debug_path)
            if attempt_index >= _OUTLINE_INVARIANT_RETRIES:
                raise
            messages = _repair_messages(prompt, outline, raw_text, str(exc), len(blocks))
    assert outline is not None
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


def _repair_messages(prompt: str, outline: Outline, raw_text: str, error_message: str, block_count: int) -> list[LLMMessage]:
    return [
        LLMMessage(role="system", content=[TextPart(text="You repair invalid JSON outline outputs. Return only valid JSON.")]),
        LLMMessage(
            role="user",
            content=[
                TextPart(
                    text=(
                        f"Validation error:\n{error_message}\n\n"
                        f"Expected block coverage: 0..{block_count - 1}\n"
                        "Keep chapter ids 1-based and ranges contiguous. Cover every block exactly once.\n\n"
                        f"Original prompt:\n{prompt}\n\n"
                        f"Parsed previous outline:\n{to_jsonable(outline)}\n\n"
                        f"Raw previous output:\n{raw_text}"
                    )
                )
            ],
        ),
    ]


def _write_outline_failure_debug(
    ctx: PipelineContext,
    error: LLMError,
    outline: Outline,
    raw_text: str,
    prompt: str,
    block_count: int,
    attempt: int,
) -> Path:
    created_at = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%fZ")
    path = ctx.paths.debug_dir / f"outline-failure-{created_at}.json"
    atomic_write_json(
        path,
        {
            "stage": "merge.outline",
            "created_at": created_at,
            "attempt": attempt,
            "input_hash": ctx.input_hash,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "block_count": block_count,
            "expected_block_id_start": 0,
            "expected_block_id_end": block_count - 1,
            "content_blocks_path": ctx.paths.content_blocks_json,
            "prompt": prompt,
            "parsed_outline": outline,
            "raw_response": raw_text,
        },
    )
    return path
