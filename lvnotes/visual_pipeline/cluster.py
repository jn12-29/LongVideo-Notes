import logging
from pathlib import Path

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
    segments = _cluster_by_visual_similarity(ctx.paths.visual_frames_dir, samples.frames, cfg.phash_low_threshold, cfg.phash_high_threshold)
    segment_list = VisualSegmentList(segments=segments)
    atomic_write_json(ctx.paths.visual_segments_json, segment_list)
    return cache_output("visual_cluster", [ctx.paths.visual_segments_json], cache_key, {"samples": samples_hash}, config_hash, None, {"item_count": len(segments)})


def _cluster_by_visual_similarity(frames_dir: Path, frames, low_threshold: int, high_threshold: int) -> list[VisualSegment]:
    if not frames:
        raise AssertionError("visual sample index must not be empty")
    signatures = [_frame_signature(frames_dir / frame.image_source_path) for frame in frames]
    segments: list[VisualSegment] = []
    start_index = 0
    for index in range(1, len(frames)):
        distance = _hamming(signatures[start_index][0], signatures[index][0])
        histogram_distance = _histogram_distance(signatures[start_index][1], signatures[index][1])
        if distance > high_threshold or (distance > low_threshold and histogram_distance > 0.25):
            segments.append(_make_segment(len(segments), frames[start_index:index], frames[index].timestamp))
            start_index = index
    segments.append(_make_segment(len(segments), frames[start_index:], frames[-1].timestamp + 1.0))
    return segments


def _make_segment(segment_id: int, frames, end: float) -> VisualSegment:
    return VisualSegment(id=segment_id, start=frames[0].timestamp, end=end, frame_ids=[frame.id for frame in frames])


def _frame_signature(path: Path) -> tuple[int, list[float]]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required for visual clustering") from exc
    with Image.open(path) as image:
        gray = image.convert("L").resize((8, 8))
        pixels = list(gray.getdata())
        mean = sum(pixels) / len(pixels)
        phash = 0
        for pixel in pixels:
            phash = (phash << 1) | int(pixel >= mean)
        histogram = image.convert("L").histogram()
        total = sum(histogram) or 1
        normalized = [value / total for value in histogram]
        return phash, normalized


def _hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _histogram_distance(left: list[float], right: list[float]) -> float:
    return sum(abs(a - b) for a, b in zip(left, right)) / 2.0
