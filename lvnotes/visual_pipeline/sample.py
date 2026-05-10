import logging

from lvnotes.core.cache import atomic_write_json, build_cache_key, hash_file, hash_json
from lvnotes.core.context import PipelineContext
from lvnotes.core.exceptions import MediaError
from lvnotes.core.paths import resolve_visual_raw_image_path
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.schemas import SampledFrame, VisualSampleIndex
from lvnotes.media.video import extract_frames

from lvnotes.visual_pipeline._common import cache_output, cached_output, read_samples

log = logging.getLogger(__name__)


def run(ctx: PipelineContext) -> StageOutput:
    cfg = ctx.config.visual_pipeline.sample
    input_hash = hash_file(ctx.source_path)
    config_hash = hash_json(cfg)
    cache_key = build_cache_key("visual_sample", {"input": input_hash, "config": config_hash})
    if not ctx.no_cache and ctx.paths.visual_sample_json.exists():
        samples = read_samples(ctx.paths.visual_sample_json)
        expected_outputs = [ctx.paths.visual_sample_json, *[resolve_visual_raw_image_path(ctx.paths, frame.image_source_path) for frame in samples.frames]]
        cached = cached_output("visual_sample", expected_outputs, cache_key)
        if cached is not None:
            return cached
    frames = extract_frames(ctx.source_path, ctx.paths.visual_raw_frames_dir, cfg.fps, "%06d.png")
    if not frames:
        raise MediaError("visual sample did not produce frames")
    samples = [SampledFrame(id=index, timestamp=frame.timestamp, image_source_path=frame.path.relative_to(ctx.paths.visual_raw_frames_dir)) for index, frame in enumerate(frames)]
    duration = frames[-1].timestamp + (1.0 / cfg.fps if cfg.fps > 0 else 0.0)
    index = VisualSampleIndex(frames=samples, duration=duration)
    atomic_write_json(ctx.paths.visual_sample_json, index)
    outputs = [ctx.paths.visual_sample_json, *[frame.path for frame in frames]]
    return cache_output("visual_sample", outputs, cache_key, {"input": input_hash}, config_hash, None, {"item_count": len(samples)})
