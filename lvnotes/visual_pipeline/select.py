import logging

from lvnotes.core.cache import atomic_write_json, build_cache_key, hash_json
from lvnotes.core.context import PipelineContext
from lvnotes.core.exceptions import CacheError
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.schemas import VisualSelection
from lvnotes.core.paths import resolve_visual_image_path

from lvnotes.visual_pipeline._common import cache_output, cached_output, read_judgements, read_samples, read_segments

log = logging.getLogger(__name__)


def run(ctx: PipelineContext) -> StageOutput:
    samples = read_samples(ctx.paths.visual_sample_json)
    segments = read_segments(ctx.paths.visual_segments_json)
    judgements = read_judgements(ctx.paths.visual_judgements_json)
    cache_key = build_cache_key("visual_select", {"segments": hash_json(segments), "judgements": hash_json(judgements), "samples": hash_json(samples), "config": hash_json({})})
    if not ctx.no_cache:
        cached = cached_output("visual_select", [ctx.paths.visual_selections_json], cache_key)
        if cached is not None:
            return cached
    frames = {frame.id: frame for frame in samples.frames}
    segment_by_id = {segment.id: segment for segment in segments.segments}
    selections: list[VisualSelection] = []
    for judgement in judgements.judgements:
        if not judgement.is_meaningful:
            continue
        segment = segment_by_id[judgement.segment_id]
        frame_id = judgement.richest_frame_id if judgement.richest_frame_id in segment.frame_ids else segment.frame_ids[len(segment.frame_ids) // 2]
        frame = frames[frame_id]
        if not resolve_visual_image_path(ctx.paths, frame.image_source_path).exists():
            raise CacheError(f"selected frame not found: {frame.image_source_path}")
        selections.append(VisualSelection(segment_id=segment.id, start=segment.start, end=segment.end, image_source_path=frame.image_source_path, medium=judgement.medium))
    atomic_write_json(ctx.paths.visual_selections_json, selections)
    return cache_output("visual_select", [ctx.paths.visual_selections_json], cache_key, {"segments": hash_json(segments), "judgements": hash_json(judgements), "samples": hash_json(samples)}, hash_json({}), None, {"item_count": len(selections)})
