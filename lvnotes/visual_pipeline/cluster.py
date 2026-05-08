import logging

from lvnotes.core.cache import atomic_write_json, build_cache_key, hash_json
from lvnotes.core.context import PipelineContext
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.schemas import VisualSegment, VisualSegmentList

from lvnotes.visual_pipeline._common import cache_output, cached_output, read_samples

log = logging.getLogger(__name__)


def run(ctx: PipelineContext) -> StageOutput:
    samples = read_samples(ctx.paths.visual_sample_json)
    cfg = ctx.config.visual_pipeline.cluster
    samples_hash = hash_json(samples)
    config_hash = hash_json(cfg)
    cache_key = build_cache_key("visual_cluster", {"samples": samples_hash, "config": config_hash})
    if not ctx.no_cache:
        cached = cached_output("visual_cluster", [ctx.paths.visual_segments_json], cache_key)
        if cached is not None:
            return cached
    segments = _cluster_by_time(samples.frames, max(1, cfg.phash_high_threshold - cfg.phash_low_threshold))
    segment_list = VisualSegmentList(segments=segments)
    atomic_write_json(ctx.paths.visual_segments_json, segment_list)
    return cache_output("visual_cluster", [ctx.paths.visual_segments_json], cache_key, {"samples": samples_hash}, config_hash, None, {"item_count": len(segments)})


def _cluster_by_time(frames, frame_span: int) -> list[VisualSegment]:
    if not frames:
        raise AssertionError("visual sample index must not be empty")
    segments: list[VisualSegment] = []
    for start_index in range(0, len(frames), frame_span):
        chunk = frames[start_index : start_index + frame_span]
        next_frame = frames[start_index + frame_span] if start_index + frame_span < len(frames) else None
        end = next_frame.timestamp if next_frame is not None else chunk[-1].timestamp + 1.0
        segments.append(VisualSegment(id=len(segments), start=chunk[0].timestamp, end=end, frame_ids=[frame.id for frame in chunk]))
    return segments
