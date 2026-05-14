import logging
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

from lvnotes.core.cache import atomic_write_json, build_cache_key, hash_file, hash_json
from lvnotes.core.config import VisualFilterConfig
from lvnotes.core.context import PipelineContext
from lvnotes.core.exceptions import MediaError
from lvnotes.core.paths import resolve_visual_filter_image_path
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.schemas import SampledFrame, VisualSampleIndex
from lvnotes.media.probe import probe_media

from lvnotes.visual_pipeline._common import cache_output, cached_output, read_samples

log = logging.getLogger(__name__)

_ALGORITHM = "pyscenedetect-content-v7"


@dataclass(frozen=True)
class SceneWindow:
    start: float
    end: float


@dataclass(frozen=True)
class SceneDetection:
    scenes: list[SceneWindow]
    threshold: float


@dataclass(frozen=True)
class CandidateFrame:
    timestamp: float
    image: Any


def run(ctx: PipelineContext) -> StageOutput:
    cfg = ctx.config.visual_pipeline.filter
    input_hash = hash_file(ctx.source_path)
    config_hash = hash_json(cfg)
    cache_key = build_cache_key("visual_filter", {"algorithm": _ALGORITHM, "input": input_hash, "config": config_hash})
    if not ctx.no_cache and ctx.paths.visual_filtered_sample_json.exists():
        filtered = read_samples(ctx.paths.visual_filtered_sample_json)
        expected_outputs = [ctx.paths.visual_filtered_sample_json, *[resolve_visual_filter_image_path(ctx.paths, frame.image_source_path) for frame in filtered.frames]]
        cached = cached_output("visual_filter", expected_outputs, cache_key)
        if cached is not None:
            return cached

    duration = _video_duration(ctx.source_path)
    detection = _detect_scenes(ctx, cfg, duration)
    log.info("visual filter selected scene threshold=%s", detection.threshold)
    candidates = _select_candidate_frames(ctx, detection.scenes, duration, cfg)
    kept = _sync_filter_output(ctx, candidates)
    filtered_index = VisualSampleIndex(frames=kept, duration=duration)
    atomic_write_json(ctx.paths.visual_filtered_sample_json, filtered_index)
    outputs = [ctx.paths.visual_filtered_sample_json, *[resolve_visual_filter_image_path(ctx.paths, frame.image_source_path) for frame in kept]]
    return cache_output(
        "visual_filter",
        outputs,
        cache_key,
        {"input": input_hash},
        config_hash,
        None,
        {"algorithm": _ALGORITHM, "item_count": len(kept), "selected_threshold": detection.threshold},
    )


def _video_duration(source_path: Path) -> float:
    probe = probe_media(source_path)
    if probe.video is None:
        raise MediaError(f"input has no video stream: {source_path}")
    return probe.duration


def _detect_scenes(ctx: PipelineContext, cfg: VisualFilterConfig, duration: float) -> SceneDetection:
    try:
        from scenedetect import detect
    except ImportError as exc:
        raise MediaError("PySceneDetect is required for visual filtering") from exc

    probe = probe_media(ctx.source_path)
    if probe.video is None:
        raise MediaError(f"input has no video stream: {ctx.source_path}")

    if cfg.threshold == "auto":
        detections = [
            SceneDetection(_detect_scenes_at_threshold(ctx.source_path, cfg, probe.video.fps, threshold, detect), threshold)
            for threshold in cfg.auto_threshold_candidates
        ]
        return _choose_auto_scene_detection(detections, duration, cfg.target_frames_per_minute)

    threshold = float(cfg.threshold)
    return SceneDetection(_detect_scenes_at_threshold(ctx.source_path, cfg, probe.video.fps, threshold, detect), threshold)


def _detect_scenes_at_threshold(source_path: Path, cfg: VisualFilterConfig, fps: float, threshold: float, detect) -> list[SceneWindow]:  # type: ignore[no-untyped-def]
    detector = _build_scene_detector(cfg, fps, threshold)
    scenes = detect(str(source_path), detector, start_in_scene=True)
    return [SceneWindow(_timecode_seconds(start), _timecode_seconds(end)) for start, end in scenes]


def _choose_auto_scene_detection(detections: list[SceneDetection], duration: float, target_frames_per_minute: float) -> SceneDetection:
    target_count = max(1, round((duration / 60.0) * target_frames_per_minute))
    return min(detections, key=lambda detection: _auto_threshold_score(detection, duration, target_count))


def _auto_threshold_score(detection: SceneDetection, duration: float, target_count: int) -> tuple[int, int, float]:
    scene_count = len(_normalize_scenes(detection.scenes, duration))
    return abs(scene_count - target_count), scene_count, -detection.threshold


def _build_scene_detector(cfg: VisualFilterConfig, fps: float, threshold: float):
    if cfg.detector == "content":
        from scenedetect import ContentDetector

        min_scene_len = max(1, int(math.ceil(cfg.min_scene_len_seconds * fps)))
        return ContentDetector(threshold=threshold, min_scene_len=min_scene_len)
    raise ValueError(f"unsupported visual filter detector: {cfg.detector}")


def _timecode_seconds(value) -> float:  # type: ignore[no-untyped-def]
    if hasattr(value, "get_seconds"):
        return float(value.get_seconds())
    return float(value)


def _select_candidate_frames(ctx: PipelineContext, scenes: list[SceneWindow], duration: float, cfg: VisualFilterConfig) -> list[CandidateFrame]:
    selected: list[CandidateFrame] = []
    selected_scores: list[float] = []
    seen_timestamps: set[float] = set()
    for scene in _normalize_scenes(scenes, duration):
        frame = _select_window_frame(ctx.source_path, scene, cfg)
        if frame is None or frame.timestamp in seen_timestamps:
            continue
        score = _score_candidate_frame(frame) if cfg.representative == "content" else 0.0
        selected.append(frame)
        selected_scores.append(score)
        seen_timestamps.add(frame.timestamp)
    if not selected:
        frame = _read_candidate_frame(ctx.source_path, 0.0)
        if frame is None:
            raise MediaError("visual filter did not produce frames")
        selected.append(frame)
        selected_scores.append(_score_candidate_frame(frame) if cfg.representative == "content" else 0.0)
    if cfg.representative == "content":
        selected, selected_scores = _filter_low_information_frames(selected, selected_scores, cfg.min_content_score)
        selected, selected_scores = _dedupe_content_frames(selected, selected_scores, cfg.duplicate_pixel_mean_threshold)
    selected.sort(key=lambda frame: frame.timestamp)
    return selected


def _filter_low_information_frames(frames: list[CandidateFrame], scores: list[float], min_content_score: float) -> tuple[list[CandidateFrame], list[float]]:
    kept = [(frame, score) for frame, score in zip(frames, scores) if score >= min_content_score]
    if not kept:
        best_index = max(range(len(frames)), key=lambda index: scores[index])
        kept = [(frames[best_index], scores[best_index])]
    return [frame for frame, _ in kept], [score for _, score in kept]


def _dedupe_content_frames(frames: list[CandidateFrame], scores: list[float], duplicate_pixel_mean_threshold: float) -> tuple[list[CandidateFrame], list[float]]:
    kept_frames: list[CandidateFrame] = []
    kept_scores: list[float] = []
    for frame, score in zip(frames, scores):
        duplicate_indexes = _find_duplicate_indexes(kept_frames, frame, duplicate_pixel_mean_threshold)
        if not duplicate_indexes:
            kept_frames.append(frame)
            kept_scores.append(score)
            continue
        best_duplicate_score = max(kept_scores[index] for index in duplicate_indexes)
        if score <= best_duplicate_score:
            continue
        kept_frames = [candidate for index, candidate in enumerate(kept_frames) if index not in duplicate_indexes]
        kept_scores = [candidate_score for index, candidate_score in enumerate(kept_scores) if index not in duplicate_indexes]
        kept_frames.append(frame)
        kept_scores.append(score)
    return kept_frames, kept_scores


def _normalize_scenes(scenes: list[SceneWindow], duration: float) -> list[SceneWindow]:
    valid = [SceneWindow(max(0.0, scene.start), min(duration, scene.end)) for scene in scenes if scene.end > scene.start]
    valid = [scene for scene in valid if scene.end > scene.start]
    if valid:
        return valid
    return [SceneWindow(0.0, duration)]


def _select_window_frame(source_path: Path, window: SceneWindow, cfg: VisualFilterConfig) -> CandidateFrame | None:
    if cfg.representative == "content":
        candidates = _read_candidate_frames(source_path, window, cfg.candidate_fps)
        if not candidates:
            return None
        return max(candidates, key=lambda frame: (_score_candidate_frame(frame), frame.timestamp))
    if cfg.representative == "last":
        return _read_candidate_frame(source_path, _window_end_limit(window))
    if cfg.representative == "middle":
        midpoint = window.start + ((window.end - window.start) / 2.0)
        return _read_candidate_frame(source_path, min(midpoint, _window_end_limit(window)))
    raise ValueError(f"unsupported representative: {cfg.representative}")


def _read_candidate_frames(source_path: Path, window: SceneWindow, fps: float) -> list[CandidateFrame]:
    import cv2

    timestamps = _candidate_timestamps(window, fps)
    capture = cv2.VideoCapture(str(source_path))
    try:
        if not capture.isOpened():
            raise MediaError(f"failed to open video: {source_path}")
        return [frame for timestamp in timestamps if (frame := _read_candidate_frame_from_capture(capture, timestamp)) is not None]
    finally:
        capture.release()


def _candidate_timestamps(window: SceneWindow, fps: float) -> list[float]:
    step = 1.0 / fps
    timestamps: list[float] = []
    timestamp = window.start
    while timestamp < window.end:
        timestamps.append(timestamp)
        timestamp += step
    midpoint = window.start + ((window.end - window.start) / 2.0)
    end_limit = _window_end_limit(window)
    return sorted({min(value, end_limit) for value in [*timestamps, midpoint, end_limit]})


def _window_end_limit(window: SceneWindow) -> float:
    if window.end <= window.start:
        return window.start
    return math.nextafter(window.end, window.start)


def _read_candidate_frame(source_path: Path, timestamp: float) -> CandidateFrame | None:
    import cv2

    capture = cv2.VideoCapture(str(source_path))
    try:
        if not capture.isOpened():
            raise MediaError(f"failed to open video: {source_path}")
        return _read_candidate_frame_from_capture(capture, timestamp)
    finally:
        capture.release()


def _read_candidate_frame_from_capture(capture, timestamp: float) -> CandidateFrame | None:  # type: ignore[no-untyped-def]
    import cv2

    capture.set(cv2.CAP_PROP_POS_MSEC, max(timestamp, 0.0) * 1000.0)
    ok, image = capture.read()
    if not ok or image is None:
        return None
    return CandidateFrame(timestamp=max(timestamp, 0.0), image=image)


def _score_candidate_frame(frame: CandidateFrame) -> float:
    import cv2

    image = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
    image = _resize_gray(image, 480)
    entropy = _image_entropy(image)
    edges = _edge_density(image)
    foreground = _foreground_density(image)
    contrast = float(image.std()) / 255.0
    blank_penalty = 0.5 if edges < 0.003 and contrast < 0.03 else 1.0
    return blank_penalty * ((0.35 * entropy) + (3.0 * edges) + (0.9 * foreground) + (0.5 * contrast))


def _find_duplicate_indexes(selected: list[CandidateFrame], current: CandidateFrame, duplicate_pixel_mean_threshold: float) -> list[int]:
    return [index for index, previous in enumerate(selected) if _is_near_duplicate(previous, current, duplicate_pixel_mean_threshold)]


def _is_near_duplicate(previous: CandidateFrame, current: CandidateFrame, duplicate_pixel_mean_threshold: float) -> bool:
    import cv2

    previous_image = cv2.cvtColor(previous.image, cv2.COLOR_BGR2GRAY)
    current_image = cv2.cvtColor(current.image, cv2.COLOR_BGR2GRAY)
    previous_small = _resize_gray(previous_image, 160)
    current_small = _resize_gray(current_image, 160)
    if previous_small.shape != current_small.shape:
        current_small = _resize_gray(current_small, previous_small.shape[1])
    diff = float(abs(previous_small.astype("float32") - current_small.astype("float32")).mean()) / 255.0
    return diff < duplicate_pixel_mean_threshold


def _resize_gray(image, width: int):  # type: ignore[no-untyped-def]
    import cv2

    height, current_width = image.shape[:2]
    if current_width <= width:
        return image
    target_height = max(1, int(height * width / current_width))
    return cv2.resize(image, (width, target_height), interpolation=cv2.INTER_AREA)


def _image_entropy(image) -> float:  # type: ignore[no-untyped-def]
    import cv2
    import numpy as np

    hist = cv2.calcHist([image], [0], None, [64], [0, 256]).ravel()
    probabilities = hist / max(float(hist.sum()), 1.0)
    probabilities = probabilities[probabilities > 0]
    return float(-(probabilities * np.log2(probabilities)).sum()) / 6.0


def _edge_density(image) -> float:  # type: ignore[no-untyped-def]
    import cv2

    edges = cv2.Canny(image, 60, 160)
    return float((edges > 0).mean())


def _foreground_density(image) -> float:  # type: ignore[no-untyped-def]
    import cv2

    blurred = cv2.GaussianBlur(image, (0, 0), 9)
    foreground = cv2.absdiff(image, blurred) > 12
    return float(foreground.mean())


def _sync_filter_output(ctx: PipelineContext, frames: list[CandidateFrame]) -> list[SampledFrame]:
    import cv2

    ctx.paths.visual_filter_frames_dir.mkdir(parents=True, exist_ok=True)
    _remove_stale_filtered_frames(ctx.paths.visual_filter_frames_dir)
    kept: list[SampledFrame] = []
    for index, frame in enumerate(frames):
        target = ctx.paths.visual_filter_frames_dir / f"{index + 1:06d}.png"
        if not cv2.imwrite(str(target), frame.image):
            raise MediaError(f"failed to write visual filter frame: {target}")
        kept.append(SampledFrame(id=index, timestamp=frame.timestamp, image_source_path=target.relative_to(ctx.paths.visual_filter_frames_dir)))
    return kept


def _remove_stale_filtered_frames(frames_dir: Path) -> None:
    for path in frames_dir.glob("*.png"):
        path.unlink()
