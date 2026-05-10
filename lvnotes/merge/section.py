import logging
from pathlib import Path

from jinja2 import Template

from lvnotes.core.cache import atomic_write_text, build_cache_key, hash_file, hash_json, hash_prompt_template
from lvnotes.core.context import PipelineContext
from lvnotes.core.parallel import run_parallel
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.paths import make_markdown_image_path
from lvnotes.core.schemas import Chapter, ContentBlock, Outline
from lvnotes.llm import LLMMessage, LLMRequestOptions, TextPart, complete_text, for_task

from lvnotes.merge._common import cache_output, cached_output, prompt_path, read_blocks, read_outline

log = logging.getLogger(__name__)


def run(ctx: PipelineContext) -> StageOutput:
    outline = read_outline(ctx.paths.outline_json)
    blocks = read_blocks(ctx.paths.content_blocks_json)
    template = prompt_path("section.jinja")
    prompt_hash = hash_prompt_template(template)
    config_hash = hash_json(ctx.config.merge.section)
    profile_hash = hash_json(ctx.config.llm.profiles[ctx.config.tasks["section"]])
    output_paths: list[Path] = []
    jobs: list[tuple[Chapter, list[ContentBlock], Path, str]] = []
    for chapter in outline.chapters:
        chapter_blocks = [block for block in blocks if chapter.block_id_start <= block.id <= chapter.block_id_end]
        path = ctx.paths.sections_dir / f"{chapter.id:03d}.md"
        cache_key = build_cache_key(
            f"section_chapter_{chapter.id:03d}",
            {"chapter_blocks": hash_json(chapter_blocks), "outline": hash_json(outline), "config": config_hash, "profile": profile_hash, "prompt": prompt_hash},
        )
        if not ctx.no_cache:
            cached = cached_output(f"section_chapter_{chapter.id:03d}", [path], cache_key)
            if cached is not None:
                output_paths.append(path)
                continue
        jobs.append((chapter, chapter_blocks, path, cache_key))
    if jobs:
        output_paths.extend(
            run_parallel(
                jobs,
                lambda job: _write_section(ctx, template, outline, job[0], job[1], job[2], job[3], config_hash, prompt_hash),
                desc="merge.section",
                unit="chapter",
                max_workers=ctx.config.merge.section.concurrent_calls,
            )
        )
    output_paths.sort()
    content_hash = hash_json([hash_file(path) for path in output_paths])
    return StageOutput(
        "section",
        output_paths,
        len(jobs) == 0,
        content_hash,
        {"item_count": len(output_paths), "cached_count": len(output_paths) - len(jobs), "job_count": len(jobs)},
    )


def _write_section(
    ctx: PipelineContext,
    template: Path,
    outline: Outline,
    chapter: Chapter,
    chapter_blocks: list[ContentBlock],
    path: Path,
    cache_key: str,
    config_hash: str,
    prompt_hash: str,
) -> Path:
    prompt = _render_prompt(ctx, template, outline, chapter_blocks)
    result = complete_text(for_task(ctx.config, "section"), [LLMMessage(role="user", content=[TextPart(text=prompt)])], LLMRequestOptions(temperature=0.3))
    atomic_write_text(path, result.text)
    cache_output(f"section_chapter_{chapter.id:03d}", [path], cache_key, {"chapter_blocks": hash_json(chapter_blocks), "outline": hash_json(outline)}, config_hash, prompt_hash)
    return path


def _render_prompt(ctx: PipelineContext, template: Path, outline: Outline, chapter_blocks: list[ContentBlock]) -> str:
    image_blocks = []
    for block in chapter_blocks:
        image_blocks.append(
            {
                "id": block.id,
                "start": f"{block.start:.3f}",
                "end": f"{block.end:.3f}",
                "topic": block.topic,
                "cleaned_text": block.cleaned_text,
                "summary": block.summary,
                "visuals": [
                    {
                        "path": make_markdown_image_path(ctx.paths, slot.image_source_path),
                        "description": slot.description,
                        "medium": slot.medium,
                    }
                    for slot in block.visuals
                ],
            }
        )
    return Template(template.read_text(encoding="utf-8")).render(outline=outline, blocks=image_blocks)
