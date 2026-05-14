import logging

from lvnotes.core.cache import atomic_write_json, build_cache_key, hash_json
from lvnotes.core.context import PipelineContext
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.schemas import RefinedSegment, VisualAlignment

from lvnotes.visual_pipeline._common import cache_output, cached_output, read_samples, read_semantic_judgements

log = logging.getLogger(__name__)


def run(ctx: PipelineContext) -> StageOutput:
    samples = read_samples(ctx.paths.visual_semantic_sample_json)
    judgements = read_semantic_judgements(ctx.paths.visual_semantic_judgements_json)
    refined = ctx.artifacts.audio.get_refined()
    samples_hash = hash_json(samples)
    judgements_hash = hash_json(judgements)
    refined_hash = hash_json(refined)
    align_config_hash = hash_json(ctx.config.visual_pipeline.align)
    cache_key = build_cache_key(
        "visual_align",
        {"semantic_samples": samples_hash, "semantic_judgements": judgements_hash, "refined": refined_hash, "config": align_config_hash},
    )
    if not ctx.no_cache:
        cached = cached_output("visual_align", [ctx.paths.visual_alignments_json], cache_key)
        if cached is not None:
            return cached
    medium_by_frame_id = {judgement.frame_id: judgement.medium for judgement in judgements.judgements}
    alignments = []
    for frame in samples.frames:
        segment, has_audio_context = _segment_for_timestamp(frame.timestamp, refined.segments, ctx.config.visual_pipeline.align.max_context_gap_seconds)
        alignments.append(
            VisualAlignment(
                segment_id=segment.id,
                frame_id=frame.id,
                timestamp=frame.timestamp,
                image_source_path=frame.image_source_path,
                medium=medium_by_frame_id[frame.id],
                has_audio_context=has_audio_context,
            )
        )
    alignments.sort(key=lambda alignment: (alignment.segment_id, alignment.timestamp, alignment.frame_id))
    atomic_write_json(ctx.paths.visual_alignments_json, alignments)
    return cache_output(
        "visual_align",
        [ctx.paths.visual_alignments_json],
        cache_key,
        {"semantic_samples": samples_hash, "semantic_judgements": judgements_hash, "refined": refined_hash, "config": align_config_hash},
        "",
        None,
        {"item_count": len(alignments)},
    )


def _segment_for_timestamp(timestamp: float, segments: list[RefinedSegment], max_context_gap_seconds: float) -> tuple[RefinedSegment, bool]:
    if not segments:
        raise AssertionError("refined transcript must not be empty")
    for segment in segments:
        if segment.start <= timestamp < segment.end:
            return segment, True
    nearest = min(segments, key=lambda segment: _segment_gap(timestamp, segment))
    return nearest, _segment_gap(timestamp, nearest) <= max_context_gap_seconds


def _segment_gap(timestamp: float, segment: RefinedSegment) -> float:
    if timestamp < segment.start:
        return segment.start - timestamp
    if timestamp >= segment.end:
        return timestamp - segment.end
    return 0.0
