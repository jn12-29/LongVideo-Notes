import logging

from lvnotes.core.cache import atomic_write_json, build_cache_key, hash_json
from lvnotes.core.context import PipelineContext
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.schemas import ContentBlock, VisualDescription, VisualSlot

from lvnotes.merge._common import cache_output, cached_output

log = logging.getLogger(__name__)


def run(ctx: PipelineContext) -> StageOutput:
    refined = ctx.artifacts.audio.get_refined()
    descriptions = _visual_descriptions(ctx)
    refined_hash = hash_json(refined)
    visual_hash = hash_json(descriptions) if descriptions else "audio_only"
    cache_key = build_cache_key("unify", {"refined": refined_hash, "visual": visual_hash})
    output_paths = [ctx.paths.content_blocks_json]
    if not ctx.no_cache:
        cached = cached_output("unify", output_paths, cache_key)
        if cached is not None:
            log.info("merge.unify cache hit input_hash=%s", ctx.input_hash)
            return cached

    blocks = [ContentBlock.from_refined(segment, _slots_for(segment.start, segment.end, descriptions)) for segment in refined.segments]
    _validate_blocks(blocks)
    atomic_write_json(ctx.paths.content_blocks_json, blocks)
    return cache_output("unify", output_paths, cache_key, {"refined": refined_hash, "visual": visual_hash}, "", None, {"item_count": len(blocks)})


def _visual_descriptions(ctx: PipelineContext) -> list[VisualDescription]:
    if ctx.artifacts.visual is None:
        return []
    return ctx.artifacts.visual.get_descriptions().descriptions


def _slots_for(start: float, end: float, descriptions: list[VisualDescription]) -> list[VisualSlot]:
    slots: list[VisualSlot] = []
    for description in descriptions:
        slot_start = max(start, description.start)
        slot_end = min(end, description.end)
        if slot_start >= slot_end:
            continue
        slots.append(
            VisualSlot(
                image_source_path=description.image_source_path,
                description=description.description,
                medium=description.medium,
                start=slot_start,
                end=slot_end,
                visual_segment_id=description.segment_id,
            )
        )
    return sorted(slots, key=lambda slot: slot.start)


def _validate_blocks(blocks: list[ContentBlock]) -> None:
    for expected_id, block in enumerate(blocks):
        if block.id != expected_id or block.start >= block.end:
            raise AssertionError("content block invariant failed")
        if any(ref >= block.id for ref in block.cross_refs):
            raise AssertionError("content block cross ref invariant failed")
        for slot in block.visuals:
            if slot.start < block.start or slot.end > block.end or slot.start >= slot.end:
                raise AssertionError("visual slot invariant failed")
