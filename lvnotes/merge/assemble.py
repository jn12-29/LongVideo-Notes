from datetime import datetime, timezone
import logging
import re

from lvnotes.core.cache import atomic_write_text, build_cache_key, hash_file, hash_json
from lvnotes.core.context import PipelineContext
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.slugs import make_chapter_anchor
from lvnotes.core.timestamps import format_hms, render_timestamp

from lvnotes.merge._common import cache_output, cached_output, read_blocks, read_outline

log = logging.getLogger(__name__)
_REF_RE = re.compile(r"\[\[REF:(\d+)\]\]")
_TS_RE = re.compile(r"\[\[TS:(\d+(?:\.\d+)?)(?:-\d+(?:\.\d+)?)?\]\]")


def run(ctx: PipelineContext) -> StageOutput:
    outline = read_outline(ctx.paths.outline_json)
    blocks = read_blocks(ctx.paths.content_blocks_json)
    section_paths = [ctx.paths.sections_dir / f"{chapter.id:03d}.md" for chapter in outline.chapters]
    sections_hash = hash_json([hash_file(path) for path in section_paths])
    cache_key = build_cache_key("assemble", {"outline": hash_json(outline), "blocks": hash_file(ctx.paths.content_blocks_json), "sections": sections_hash, "config": hash_json(ctx.config.merge.assemble)})
    output_paths = [ctx.paths.output_note_md, ctx.paths.cache_note_md]
    if not ctx.no_cache:
        cached = cached_output("assemble", output_paths, cache_key, manifest_output_path=ctx.paths.cache_note_md)
        if cached is not None:
            return cached

    note = _assemble_note(ctx, outline, blocks, section_paths)
    atomic_write_text(ctx.paths.cache_note_md, note)
    atomic_write_text(ctx.paths.output_note_md, note)
    return cache_output("assemble", output_paths, cache_key, {"outline": hash_json(outline), "blocks": hash_file(ctx.paths.content_blocks_json), "sections": sections_hash}, hash_json(ctx.config.merge.assemble), None, manifest_output_path=ctx.paths.cache_note_md)


def _assemble_note(ctx: PipelineContext, outline, blocks, section_paths: list) -> str:
    anchors = {chapter.id: make_chapter_anchor(chapter.id, chapter.title) for chapter in outline.chapters}
    block_to_chapter = {block_id: chapter.id for chapter in outline.chapters for block_id in range(chapter.block_id_start, chapter.block_id_end + 1)}
    parts: list[str] = []
    if ctx.config.merge.assemble.include_metadata:
        parts.append(_frontmatter(ctx))
    title = ctx.config.merge.assemble.top_title or ctx.source_path.stem
    parts.append(f"# {title}\n")
    if ctx.config.merge.assemble.include_toc:
        parts.append("\n".join(f"- [{chapter.title}](#{anchors[chapter.id]})" for chapter in outline.chapters) + "\n")
    for chapter, path in zip(outline.chapters, section_paths):
        text = path.read_text(encoding="utf-8")
        text = _strip_section_heading(text, chapter.title)
        text = _render_refs(text, block_to_chapter, anchors, current_chapter_id=chapter.id)
        text = _render_timestamps(ctx, text)
        text = _normalize_markdown_spacing(text)
        parts.append(f"## {chapter.title}\n{text.strip()}\n")
    return "\n".join(parts).rstrip() + "\n"


def _strip_section_heading(text: str, chapter_title: str) -> str:
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return ""
    heading = lines[0].strip()
    if heading.startswith("#"):
        title = heading.lstrip("#").strip()
        if title == chapter_title or title.endswith(f" {chapter_title}"):
            return "\n".join(lines[1:]).lstrip()
    return "\n".join(lines)


def _frontmatter(ctx: PipelineContext) -> str:
    duration = ctx.artifacts.audio.get_duration()
    slide_judge = ctx.config.tasks["slide_judge"] if ctx.mode == "multimodal" else "null"
    slide_describe = ctx.config.tasks["slide_describe"] if ctx.mode == "multimodal" else "null"
    return (
        "---\n"
        f"source_path: {ctx.source_path}\n"
        f"duration: {format_hms(duration)}\n"
        f"generated_at: {datetime.now(timezone.utc).isoformat()}\n"
        f"mode: {ctx.mode}\n"
        "llm_profiles:\n"
        f"  segment: {ctx.config.tasks['segment']}\n"
        f"  refine: {ctx.config.tasks['refine']}\n"
        f"  outline: {ctx.config.tasks['outline']}\n"
        f"  section: {ctx.config.tasks['section']}\n"
        f"  slide_judge: {slide_judge}\n"
        f"  slide_describe: {slide_describe}\n"
        "---\n"
    )


def _render_refs(text: str, block_to_chapter: dict[int, int], anchors: dict[int, str], *, current_chapter_id: int | None = None) -> str:
    def replace(match: re.Match[str]) -> str:
        block_id = int(match.group(1))
        chapter_id = block_to_chapter.get(block_id)
        if chapter_id is None:
            log.warning("assemble: cross_ref §%s not resolvable, rendered as plain text", block_id + 1)
            return f"§{block_id + 1}"
        if chapter_id == current_chapter_id:
            return ""
        return f"[§{block_id + 1}](#{anchors[chapter_id]})"

    return _REF_RE.sub(replace, text)


def _render_timestamps(ctx: PipelineContext, text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        seconds = float(match.group(1))
        label = render_timestamp(seconds, ctx.config.merge.assemble.timestamp_format)
        url_template = ctx.config.merge.assemble.video_url_template
        if url_template is None:
            return label
        url = url_template.format(
            seconds=f"{seconds:.3f}",
            seconds_int=int(seconds),
            source_path=ctx.source_path,
            source_filename=ctx.source_path.name,
            hms=format_hms(seconds),
        )
        return f"[{label}]({url})"

    return _TS_RE.sub(replace, text)


def _normalize_markdown_spacing(text: str) -> str:
    text = re.sub(r"(\[\d{2}:\d{2}:\d{2}\])\s*([。！？；，、,.!?;:])", r"\2\1", text)
    text = re.sub(r" +([。！？；，、,.!?;:])", r"\1", text)
    text = re.sub(r"(\[\d{2}:\d{2}:\d{2}\])(?!\s|$)", r"\1 ", text)
    return "\n".join(line.rstrip() for line in text.splitlines())
