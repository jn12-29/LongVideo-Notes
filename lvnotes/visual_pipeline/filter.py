import logging
from pathlib import Path
import shutil

from lvnotes.core.cache import atomic_write_json, build_cache_key, hash_json
from lvnotes.core.config import VisualFilterConfig, VisualFilterVariantConfig
from lvnotes.core.context import PipelineContext
from lvnotes.core.paths import resolve_visual_filter_image_path, resolve_visual_raw_image_path
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.schemas import SampledFrame, VisualSampleIndex

from lvnotes.visual_pipeline._common import cache_output, cached_output, read_samples

log = logging.getLogger(__name__)

FrameSignature = tuple[int, list[float], list[float]]


def run(ctx: PipelineContext) -> StageOutput:
    samples = read_samples(ctx.paths.visual_sample_json)
    cfg = ctx.config.visual_pipeline.filter
    variants = _resolve_variants(ctx)
    _validate_active_variant(cfg, variants)
    samples_hash = hash_json(samples)
    variants_hash = hash_json(variants)
    config_hash = hash_json(cfg.model_copy(update={"variants_file": None}))
    cache_key = build_cache_key("visual_filter", {"samples": samples_hash, "config": config_hash, "variants": variants_hash})
    if not ctx.no_cache and ctx.paths.visual_filtered_sample_json.exists():
        filtered = read_samples(ctx.paths.visual_filtered_sample_json)
        expected_outputs = [ctx.paths.visual_filtered_sample_json, *[resolve_visual_filter_image_path(ctx.paths, frame.image_source_path) for frame in filtered.frames]]
        cached = cached_output("visual_filter", expected_outputs, cache_key)
        if cached is not None:
            if not _variant_outputs_complete(ctx, variants):
                _write_filter_variants(ctx, samples, variants)
            return cached

    variant_outputs = _write_filter_variants(ctx, samples, variants)
    active = variant_outputs[cfg.active_variant]
    kept = _sync_stable_filter_output(ctx, active.frames, active.frames_dir)
    filtered_index = VisualSampleIndex(frames=kept, duration=samples.duration)
    atomic_write_json(ctx.paths.visual_filtered_sample_json, filtered_index)
    outputs = [ctx.paths.visual_filtered_sample_json, *[resolve_visual_filter_image_path(ctx.paths, frame.image_source_path) for frame in kept]]
    return cache_output("visual_filter", outputs, cache_key, {"samples": samples_hash}, config_hash, None, {"item_count": len(kept)})


class FilterVariantOutput:
    def __init__(self, name: str, slug: str, adjacent_frames: list[SampledFrame], frames: list[SampledFrame], frames_dir: Path) -> None:
        self.name = name
        self.slug = slug
        self.adjacent_frames = adjacent_frames
        self.frames = frames
        self.frames_dir = frames_dir


def _resolve_variants(ctx: PipelineContext) -> list[VisualFilterVariantConfig]:
    if ctx.config.filter_variants is not None:
        return ctx.config.filter_variants.variants
    cfg = ctx.config.visual_pipeline.filter
    return [
        VisualFilterVariantConfig(
            name="default",
            phash_threshold=cfg.phash_threshold,
            histogram_threshold=cfg.histogram_threshold,
            duplicate_phash_threshold=cfg.duplicate_phash_threshold,
            duplicate_histogram_threshold=cfg.duplicate_histogram_threshold,
            duplicate_pixel_threshold=cfg.duplicate_pixel_threshold,
            max_static_seconds=cfg.max_static_seconds,
            crop=cfg.crop,
        )
    ]


def _validate_active_variant(cfg: VisualFilterConfig, variants: list[VisualFilterVariantConfig]) -> None:
    if cfg.active_variant not in {variant.name for variant in variants}:
        raise ValueError(f"active_variant not found in filter variants: {cfg.active_variant}")


def _write_filter_variants(ctx: PipelineContext, samples: VisualSampleIndex, variants: list[VisualFilterVariantConfig]) -> dict[str, FilterVariantOutput]:
    if not samples.frames:
        raise AssertionError("visual sample index must not be empty")
    _reset_dir(ctx.paths.visual_filter_variants_dir)
    outputs: dict[str, FilterVariantOutput] = {}
    for variant in variants:
        output = _write_filter_variant(ctx, samples, variant)
        outputs[variant.name] = output
    _write_summary(ctx, outputs, ctx.config.visual_pipeline.filter.active_variant, variants)
    return outputs


def _write_filter_variant(ctx: PipelineContext, samples: VisualSampleIndex, cfg: VisualFilterVariantConfig) -> FilterVariantOutput:
    slug = _variant_slug(cfg)
    variant_dir = ctx.paths.visual_filter_variants_dir / slug
    adjacent_frames_dir = variant_dir / "adjacent_frames"
    final_frames_dir = variant_dir / "frames"
    adjacent_frames_dir.mkdir(parents=True, exist_ok=True)
    final_frames_dir.mkdir(parents=True, exist_ok=True)
    adjacent = _filter_adjacent_frames(ctx, samples, cfg)
    _copy_frames(ctx, adjacent, adjacent_frames_dir)
    adjacent_index = VisualSampleIndex(frames=[_relative_to(frame, adjacent_frames_dir) for frame in adjacent], duration=samples.duration)
    atomic_write_json(variant_dir / "adjacent_sample.json", adjacent_index)
    final = _filter_global_duplicates(ctx, adjacent, cfg)
    _copy_frames(ctx, final, final_frames_dir)
    final_index = VisualSampleIndex(frames=[_relative_to(frame, final_frames_dir) for frame in final], duration=samples.duration)
    atomic_write_json(variant_dir / "filtered_sample.json", final_index)
    return FilterVariantOutput(cfg.name, slug, adjacent_index.frames, final_index.frames, final_frames_dir)


def _filter_adjacent_frames(ctx: PipelineContext, samples: VisualSampleIndex, cfg: VisualFilterVariantConfig) -> list[SampledFrame]:
    kept: list[SampledFrame] = []
    last_signature: FrameSignature | None = None
    last_kept_timestamp: float | None = None
    for frame in samples.frames:
        source = resolve_visual_raw_image_path(ctx.paths, frame.image_source_path)
        signature = _frame_signature(source, cfg.crop)
        should_keep = last_signature is None or _is_distinct(last_signature, signature, cfg.phash_threshold, cfg.histogram_threshold)
        if not should_keep and cfg.max_static_seconds is not None and last_kept_timestamp is not None:
            should_keep = frame.timestamp - last_kept_timestamp >= cfg.max_static_seconds
        if should_keep:
            kept.append(frame)
            last_signature = signature
            last_kept_timestamp = frame.timestamp
    return kept


def _filter_global_duplicates(ctx: PipelineContext, frames: list[SampledFrame], cfg: VisualFilterVariantConfig) -> list[SampledFrame]:
    kept: list[SampledFrame] = []
    kept_signatures: list[FrameSignature] = []
    for frame in frames:
        signature = _frame_signature(resolve_visual_raw_image_path(ctx.paths, frame.image_source_path), cfg.crop)
        if _is_global_duplicate(signature, kept_signatures, cfg.duplicate_phash_threshold, cfg.duplicate_histogram_threshold, cfg.duplicate_pixel_threshold):
            continue
        kept.append(frame)
        kept_signatures.append(signature)
    return kept


def _copy_frames(ctx: PipelineContext, frames: list[SampledFrame], target_dir: Path) -> None:
    for frame in frames:
        source = resolve_visual_raw_image_path(ctx.paths, frame.image_source_path)
        shutil.copy2(source, target_dir / frame.image_source_path.name)


def _sync_stable_filter_output(ctx: PipelineContext, frames: list[SampledFrame], source_dir: Path) -> list[SampledFrame]:
    ctx.paths.visual_filter_frames_dir.mkdir(parents=True, exist_ok=True)
    _remove_stale_filtered_frames(ctx.paths.visual_filter_frames_dir)
    kept: list[SampledFrame] = []
    for frame in frames:
        source = source_dir / frame.image_source_path.name
        target = ctx.paths.visual_filter_frames_dir / frame.image_source_path.name
        shutil.copy2(source, target)
        kept.append(SampledFrame(id=frame.id, timestamp=frame.timestamp, image_source_path=target.relative_to(ctx.paths.visual_filter_frames_dir)))
    return kept


def _relative_to(frame: SampledFrame, directory: Path) -> SampledFrame:
    return SampledFrame(id=frame.id, timestamp=frame.timestamp, image_source_path=(directory / frame.image_source_path.name).relative_to(directory))


def _write_summary(ctx: PipelineContext, outputs: dict[str, FilterVariantOutput], active_variant: str, variants: list[VisualFilterVariantConfig]) -> None:
    variant_by_name = {variant.name: variant for variant in variants}
    summary = []
    for name, output in outputs.items():
        summary.append(
            {
                "name": name,
                "slug": output.slug,
                "active": name == active_variant,
                "adjacent_count": len(output.adjacent_frames),
                "final_count": len(output.frames),
                "frame_ids": [frame.id for frame in output.frames],
                "frame_filenames": [frame.image_source_path.name for frame in output.frames],
                "thresholds": variant_by_name[name],
            }
        )
    atomic_write_json(ctx.paths.visual_filter_variants_dir / "summary.json", summary)


def _variant_slug(cfg: VisualFilterVariantConfig) -> str:
    slug = f"{cfg.name}-p{_format_number(cfg.phash_threshold)}-h{_format_number(cfg.histogram_threshold)}"
    slug += f"-dupP{_format_number(cfg.duplicate_phash_threshold)}-dupH{_format_number(cfg.duplicate_histogram_threshold)}-dupPix{_format_number(cfg.duplicate_pixel_threshold)}"
    if cfg.max_static_seconds is not None:
        slug += f"-static{_format_number(cfg.max_static_seconds)}"
    return slug


def _variant_outputs_complete(ctx: PipelineContext, variants: list[VisualFilterVariantConfig]) -> bool:
    if not (ctx.paths.visual_filter_variants_dir / "summary.json").exists():
        return False
    for variant in variants:
        variant_dir = ctx.paths.visual_filter_variants_dir / _variant_slug(variant)
        if not (variant_dir / "adjacent_sample.json").exists() or not (variant_dir / "filtered_sample.json").exists():
            return False
        try:
            adjacent = read_samples(variant_dir / "adjacent_sample.json")
            final = read_samples(variant_dir / "filtered_sample.json")
        except Exception:
            return False
        if not all((variant_dir / "adjacent_frames" / frame.image_source_path.name).exists() for frame in adjacent.frames):
            return False
        if not all((variant_dir / "frames" / frame.image_source_path.name).exists() for frame in final.frames):
            return False
    return True


def _format_number(value: float | int) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _remove_stale_filtered_frames(frames_dir: Path) -> None:
    for path in frames_dir.glob("*.png"):
        path.unlink()


def _reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _frame_signature(path: Path, crop) -> FrameSignature:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required for visual filtering") from exc
    with Image.open(path) as image:
        area = _crop_image(image, crop)
        gray = area.convert("L").resize((8, 8))
        pixels = list(gray.getdata())
        mean = sum(pixels) / len(pixels)
        phash = 0
        for pixel in pixels:
            phash = (phash << 1) | int(pixel >= mean)
        histogram = area.convert("L").histogram()
        total = sum(histogram) or 1
        normalized = [value / total for value in histogram]
        pixel_sample = [value / 255 for value in area.convert("L").resize((64, 36)).getdata()]
        return phash, normalized, pixel_sample


def _crop_image(image, crop):
    if crop is None:
        return image.copy()
    width, height = image.size
    box = (
        round(crop.left * width),
        round(crop.top * height),
        round(crop.right * width),
        round(crop.bottom * height),
    )
    return image.crop(box)


def _is_distinct(left: FrameSignature, right: FrameSignature, phash_threshold: int, histogram_threshold: float) -> bool:
    return _hamming(left[0], right[0]) > phash_threshold or _histogram_distance(left[1], right[1]) > histogram_threshold


def _is_global_duplicate(signature: FrameSignature, kept_signatures: list[FrameSignature], phash_threshold: int, histogram_threshold: float, pixel_threshold: float) -> bool:
    return any(
        _hamming(signature[0], kept[0]) <= phash_threshold
        and _histogram_distance(signature[1], kept[1]) <= histogram_threshold
        and _pixel_distance(signature[2], kept[2]) <= pixel_threshold
        for kept in kept_signatures
    )


def _hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _histogram_distance(left: list[float], right: list[float]) -> float:
    return sum(abs(a - b) for a, b in zip(left, right)) / 2.0


def _pixel_distance(left: list[float], right: list[float]) -> float:
    return sum(abs(a - b) for a, b in zip(left, right)) / len(left)
