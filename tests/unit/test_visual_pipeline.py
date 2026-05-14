import math
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from lvnotes.core.cache import atomic_write_json, read_json_file
from lvnotes.core.config import AppConfig
from lvnotes.core.context import ArtifactBundle, PipelineContext
from lvnotes.core.exceptions import LLMError
from lvnotes.core.paths import build_paths, resolve_visual_filter_image_path, resolve_visual_semantic_image_path
from lvnotes.core.schemas import RefinedSegment, RefinedTranscript, SampledFrame, VisualAlignment, VisualDescription, VisualSampleIndex, VisualSemanticJudgement, VisualSemanticJudgementList
from lvnotes.media import video
from lvnotes.visual_pipeline import align, describe, filter, semantic_filter


def test_extract_frames_removes_stale_pattern_frames(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"video")
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    (frames_dir / "000001.png").write_bytes(b"old")
    (frames_dir / "keep.txt").write_bytes(b"keep")

    monkeypatch.setattr(video, "probe_media", lambda path: SimpleNamespace(video=object()))

    def run_command(args: list[str], tool_name: str) -> None:
        output = Path(args[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        (output.parent / "000001.png").write_bytes(b"new")
        (output.parent / "000002.png").write_bytes(b"new")

    monkeypatch.setattr(video, "_run_command", run_command)

    frames = video.extract_frames(source, frames_dir, 1.0, "%06d.png")

    assert [frame.path.name for frame in frames] == ["000001.png", "000002.png"]
    assert (frames_dir / "000001.png").read_bytes() == b"new"
    assert (frames_dir / "keep.txt").read_bytes() == b"keep"


def test_filter_writes_scene_representatives_to_filter_frames(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    object.__setattr__(ctx.config.visual_pipeline.filter, "representative", "last")
    monkeypatch.setattr(filter, "_video_duration", lambda path: 3.0)
    monkeypatch.setattr(filter, "_detect_scenes", lambda ctx, cfg, duration: _detection([filter.SceneWindow(0.0, 2.0), filter.SceneWindow(2.0, 3.0)]))
    monkeypatch.setattr(filter, "_read_candidate_frame", lambda source_path, timestamp: _candidate(timestamp, "text"))

    filter.run(ctx)

    filtered = filter.read_samples(ctx.paths.visual_filtered_sample_json)
    assert filtered.frames == [
        SampledFrame(0, math.nextafter(2.0, 0.0), Path("000001.png")),
        SampledFrame(1, math.nextafter(3.0, 2.0), Path("000002.png")),
    ]
    assert (ctx.paths.visual_filter_frames_dir / "000001.png").exists()
    assert (ctx.paths.visual_filter_frames_dir / "000002.png").exists()


def test_filter_middle_representative_uses_nearest_scene_midpoint(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    object.__setattr__(ctx.config.visual_pipeline.filter, "representative", "middle")
    monkeypatch.setattr(filter, "_video_duration", lambda path: 3.0)
    monkeypatch.setattr(filter, "_detect_scenes", lambda ctx, cfg, duration: _detection([filter.SceneWindow(0.0, 3.0)]))
    monkeypatch.setattr(filter, "_read_candidate_frame", lambda source_path, timestamp: _candidate(timestamp, "text"))

    filter.run(ctx)

    filtered = filter.read_samples(ctx.paths.visual_filtered_sample_json)
    assert filtered.frames == [SampledFrame(0, 1.5, Path("000001.png"))]


def test_filter_middle_representative_stays_inside_tiny_windows(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    object.__setattr__(ctx.config.visual_pipeline.filter, "representative", "middle")
    first_start = math.nextafter(1.0, 0.0)
    second_end = math.nextafter(1.0, 2.0)
    scenes = [filter.SceneWindow(first_start, 1.0), filter.SceneWindow(1.0, second_end)]
    monkeypatch.setattr(filter, "_read_candidate_frame", lambda source_path, timestamp: _candidate(timestamp, "text"))

    selected = filter._select_candidate_frames(ctx, scenes, second_end, ctx.config.visual_pipeline.filter)

    assert [frame.timestamp for frame in selected] == [first_start, 1.0]
    assert scenes[0].start <= selected[0].timestamp < scenes[0].end
    assert scenes[1].start <= selected[1].timestamp < scenes[1].end


def test_filter_content_representative_prefers_high_information_frame(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    monkeypatch.setattr(filter, "_video_duration", lambda path: 3.0)
    monkeypatch.setattr(filter, "_detect_scenes", lambda ctx, cfg, duration: _detection([filter.SceneWindow(0.0, 3.0)]))
    monkeypatch.setattr(filter, "_read_candidate_frames", lambda source_path, window, fps: [_candidate(0.0, "blank"), _candidate(1.0, "text"), _candidate(2.0, "dark")])

    filter.run(ctx)

    filtered = filter.read_samples(ctx.paths.visual_filtered_sample_json)
    assert filtered.frames == [SampledFrame(0, 1.0, Path("000001.png"))]


def test_filter_content_representative_drops_adjacent_duplicate_frames(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    monkeypatch.setattr(filter, "_video_duration", lambda path: 2.0)
    monkeypatch.setattr(filter, "_detect_scenes", lambda ctx, cfg, duration: _detection([filter.SceneWindow(0.0, 1.0), filter.SceneWindow(1.0, 2.0)]))
    monkeypatch.setattr(filter, "_read_candidate_frames", lambda source_path, window, fps: [_candidate(window.start, "text")])

    filter.run(ctx)

    filtered = filter.read_samples(ctx.paths.visual_filtered_sample_json)
    assert filtered.frames == [SampledFrame(0, 0.0, Path("000001.png"))]


def test_filter_content_representative_filters_low_score_frames(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    frames = [_candidate(0.0, "blank"), _candidate(1.0, "text"), _candidate(2.0, "text")]
    scenes = [filter.SceneWindow(0.0, 1.0), filter.SceneWindow(1.0, 2.0), filter.SceneWindow(2.0, 3.0)]
    scores = {0.0: 0.3, 1.0: 0.6, 2.0: 0.7}
    monkeypatch.setattr(filter, "_select_window_frame", lambda source_path, window, cfg: frames[int(window.start)])
    monkeypatch.setattr(filter, "_score_candidate_frame", lambda frame: scores[frame.timestamp])
    monkeypatch.setattr(filter, "_is_near_duplicate", lambda previous, current, duplicate_pixel_mean_threshold: False)

    selected = filter._select_candidate_frames(ctx, scenes, 3.0, ctx.config.visual_pipeline.filter)

    assert selected == [frames[1], frames[2]]


def test_filter_content_representative_keeps_output_chronological_after_duplicate_replacement(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    frames = [_candidate(0.0, "text"), _candidate(1.0, "dark"), _candidate(2.0, "text")]
    scenes = [filter.SceneWindow(0.0, 1.0), filter.SceneWindow(1.0, 2.0), filter.SceneWindow(2.0, 3.0)]
    scores = {0.0: 0.6, 1.0: 0.7, 2.0: 0.8}
    monkeypatch.setattr(filter, "_select_window_frame", lambda source_path, window, cfg: frames[int(window.start)])
    monkeypatch.setattr(filter, "_score_candidate_frame", lambda frame: scores[frame.timestamp])
    monkeypatch.setattr(filter, "_is_near_duplicate", lambda previous, current, duplicate_pixel_mean_threshold: previous.timestamp == 0.0 and current.timestamp == 2.0)

    selected = filter._select_candidate_frames(ctx, scenes, 3.0, ctx.config.visual_pipeline.filter)

    assert selected == [frames[1], frames[2]]


def test_filter_no_cache_rebuilds_stable_outputs(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    object.__setattr__(ctx, "no_cache", True)
    (ctx.paths.visual_filter_frames_dir / "stale.png").write_bytes(b"old")
    object.__setattr__(ctx.config.visual_pipeline.filter, "representative", "last")
    monkeypatch.setattr(filter, "_video_duration", lambda path: 1.0)
    monkeypatch.setattr(filter, "_detect_scenes", lambda ctx, cfg, duration: _detection([filter.SceneWindow(0.0, 1.0)]))
    monkeypatch.setattr(filter, "_read_candidate_frame", lambda source_path, timestamp: _candidate(0.0, "text"))

    filter.run(ctx)

    assert not (ctx.paths.visual_filter_frames_dir / "stale.png").exists()
    assert (ctx.paths.visual_filter_frames_dir / "000001.png").exists()


def test_filter_no_cache_bypasses_valid_cache(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    object.__setattr__(ctx.config.visual_pipeline.filter, "representative", "last")
    monkeypatch.setattr(filter, "_video_duration", lambda path: 2.0)
    monkeypatch.setattr(filter, "_detect_scenes", lambda ctx, cfg, duration: _detection([filter.SceneWindow(0.0, 2.0)]))
    calls = []

    def read_candidate(source_path: Path, timestamp: float) -> filter.CandidateFrame:
        calls.append(timestamp)
        return _candidate(0.5 if len(calls) == 1 else 1.5, "text")

    monkeypatch.setattr(filter, "_read_candidate_frame", read_candidate)

    first = filter.run(ctx)
    object.__setattr__(ctx, "no_cache", True)
    second = filter.run(ctx)

    filtered = filter.read_samples(ctx.paths.visual_filtered_sample_json)
    assert first.cache_hit is False
    assert second.cache_hit is False
    assert calls == [math.nextafter(2.0, 0.0), math.nextafter(2.0, 0.0)]
    assert filtered.frames == [SampledFrame(0, 1.5, Path("000001.png"))]


def test_filter_cache_hit_does_not_rewrite_stable_outputs(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    object.__setattr__(ctx.config.visual_pipeline.filter, "representative", "last")
    monkeypatch.setattr(filter, "_video_duration", lambda path: 1.0)
    monkeypatch.setattr(filter, "_detect_scenes", lambda ctx, cfg, duration: _detection([filter.SceneWindow(0.0, 1.0)]))
    monkeypatch.setattr(filter, "_read_candidate_frame", lambda source_path, timestamp: _candidate(0.0, "text"))

    filter.run(ctx)
    stable = ctx.paths.visual_filter_frames_dir / "000001.png"
    stable.write_bytes(b"stable")

    output = filter.run(ctx)

    assert output.cache_hit is True
    assert stable.read_bytes() == b"stable"


def test_filter_empty_scene_result_falls_back_to_full_duration(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    object.__setattr__(ctx.config.visual_pipeline.filter, "representative", "last")
    monkeypatch.setattr(filter, "_video_duration", lambda path: 3.0)
    monkeypatch.setattr(filter, "_detect_scenes", lambda ctx, cfg, duration: _detection([]))
    monkeypatch.setattr(filter, "_read_candidate_frame", lambda source_path, timestamp: _candidate(timestamp, "text"))

    filter.run(ctx)

    filtered = filter.read_samples(ctx.paths.visual_filtered_sample_json)
    assert filtered.frames == [SampledFrame(0, math.nextafter(3.0, 0.0), Path("000001.png"))]


def test_filter_keeps_one_representative_per_scene(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    object.__setattr__(ctx.config.visual_pipeline.filter, "representative", "last")
    monkeypatch.setattr(filter, "_video_duration", lambda path: 3.0)
    monkeypatch.setattr(filter, "_detect_scenes", lambda ctx, cfg, duration: _detection([filter.SceneWindow(0.0, 3.0)]))
    monkeypatch.setattr(filter, "_read_candidate_frame", lambda source_path, timestamp: _candidate(timestamp, "text"))

    filter.run(ctx)

    filtered = filter.read_samples(ctx.paths.visual_filtered_sample_json)
    assert filtered.frames == [SampledFrame(0, math.nextafter(3.0, 0.0), Path("000001.png"))]


def test_filter_candidate_timestamps_do_not_seek_at_window_end() -> None:
    timestamps = filter._candidate_timestamps(filter.SceneWindow(0.0, 1.0), 10.0)

    assert timestamps[-1] == math.nextafter(1.0, 0.0)
    assert all(timestamp < 1.0 for timestamp in timestamps)


def test_filter_candidate_timestamps_stay_inside_tiny_nonzero_windows() -> None:
    window = filter.SceneWindow(0.1234566, 0.1234569)
    timestamps = filter._candidate_timestamps(window, 10.0)

    assert timestamps[0] == window.start
    assert all(window.start <= timestamp < window.end for timestamp in timestamps)


def test_filter_read_candidate_frame_preserves_window_boundary_precision() -> None:
    class Capture:
        def __init__(self) -> None:
            self.positions = []

        def set(self, prop: int, value: float) -> None:
            self.positions.append((prop, value))

        def read(self):  # type: ignore[no-untyped-def]
            return True, object()

    capture = Capture()

    timestamp = math.nextafter(1.0, 0.0)
    frame = filter._read_candidate_frame_from_capture(capture, timestamp)

    assert frame is not None
    assert frame == filter.CandidateFrame(timestamp=timestamp, image=frame.image)
    assert capture.positions[-1][1] == pytest.approx(timestamp * 1000.0)


def test_filter_auto_threshold_selects_scene_count_nearest_target() -> None:
    detections = [
        filter.SceneDetection([filter.SceneWindow(float(index), float(index + 1)) for index in range(20)], 10.0),
        filter.SceneDetection([filter.SceneWindow(float(index), float(index + 1)) for index in range(9)], 27.0),
        filter.SceneDetection([filter.SceneWindow(float(index), float(index + 1)) for index in range(3)], 40.0),
    ]

    detection = filter._choose_auto_scene_detection(detections, 570.0, 1.2)

    assert detection.threshold == 27.0


def test_filter_auto_threshold_tie_prefers_higher_threshold() -> None:
    detections = [
        filter.SceneDetection([filter.SceneWindow(float(index), float(index + 1)) for index in range(15)], 25.0),
        filter.SceneDetection([filter.SceneWindow(float(index), float(index + 1)) for index in range(15)], 27.0),
    ]

    detection = filter._choose_auto_scene_detection(detections, 570.0, 1.5)

    assert detection.threshold == 27.0


def test_filter_build_scene_detector_ceil_min_scene_len(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class ContentDetector:
        def __init__(self, *, threshold: float, min_scene_len: int) -> None:
            self.threshold = threshold
            self.min_scene_len = min_scene_len

    monkeypatch.setitem(sys.modules, "scenedetect", SimpleNamespace(ContentDetector=ContentDetector))
    cfg = _config(Path(".")).visual_pipeline.filter
    object.__setattr__(cfg, "min_scene_len_seconds", 0.24)

    detector = filter._build_scene_detector(cfg, 10.0, 27.0)

    assert getattr(detector, "threshold") == 27.0
    assert getattr(detector, "min_scene_len") == 3


def test_semantic_filter_copies_meaningful_frames(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    _write_filter_frames(ctx, ["000001.png", "000002.png"])
    atomic_write_json(
        ctx.paths.visual_filtered_sample_json,
        VisualSampleIndex([SampledFrame(1, 1.0, Path("000001.png")), SampledFrame(2, 2.0, Path("000002.png"))], 3.0),
    )
    monkeypatch.setattr(
        semantic_filter,
        "_judge_frames",
        lambda ctx, samples, template: VisualSemanticJudgementList(
            [VisualSemanticJudgement(1, "ppt", True, "content slide"), VisualSemanticJudgement(2, "speaker", False, "speaker only")]
        ),
    )

    semantic_filter.run(ctx)

    semantic = filter.read_samples(ctx.paths.visual_semantic_sample_json)
    assert semantic.frames == [SampledFrame(1, 1.0, Path("000001.png"))]
    assert (ctx.paths.visual_semantic_frames_dir / "000001.png").read_bytes() == b"frame"
    assert not (ctx.paths.visual_semantic_frames_dir / "000002.png").exists()


def test_semantic_filter_cache_key_includes_filter_frame_content(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    _write_filter_frames(ctx, ["000001.png"])
    atomic_write_json(ctx.paths.visual_filtered_sample_json, VisualSampleIndex([SampledFrame(1, 1.0, Path("000001.png"))], 2.0))
    calls = []

    def judge_frames(ctx, samples, template):  # type: ignore[no-untyped-def]
        calls.append((ctx, samples, template))
        return VisualSemanticJudgementList([VisualSemanticJudgement(1, "ppt", True, "content slide")])

    monkeypatch.setattr(semantic_filter, "_judge_frames", judge_frames)

    first = semantic_filter.run(ctx)
    second = semantic_filter.run(ctx)
    (ctx.paths.visual_filter_frames_dir / "000001.png").write_bytes(b"changed")
    third = semantic_filter.run(ctx)

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert third.cache_hit is False
    assert len(calls) == 2
    assert (ctx.paths.visual_semantic_frames_dir / "000001.png").read_bytes() == b"changed"


def test_semantic_filter_validates_exactly_one_judgement_per_frame() -> None:
    semantic_filter._validate_judgements(VisualSemanticJudgementList([VisualSemanticJudgement(1, "ppt", True, "ok")]), {1})
    with pytest.raises(LLMError):
        semantic_filter._validate_judgements(VisualSemanticJudgementList([VisualSemanticJudgement(1, "invalid", True, "ok")]), {1})
    with pytest.raises(LLMError):
        semantic_filter._validate_judgements(VisualSemanticJudgementList([VisualSemanticJudgement(1, "ppt", True, "ok")]), {1, 2})


def test_align_maps_semantic_frames_to_refined_segments(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, mode="multimodal")
    atomic_write_json(
        ctx.paths.visual_semantic_sample_json,
        VisualSampleIndex([SampledFrame(1, 1.0, Path("000001.png")), SampledFrame(2, 12.0, Path("000002.png")), SampledFrame(3, 25.0, Path("000003.png"))], 30.0),
    )
    atomic_write_json(
        ctx.paths.visual_semantic_judgements_json,
        VisualSemanticJudgementList([VisualSemanticJudgement(1, "ppt", True, "ok"), VisualSemanticJudgement(2, "chart", True, "ok"), VisualSemanticJudgement(3, "ppt", True, "ok")]),
    )
    refined = RefinedTranscript(
        [
            RefinedSegment(0, 0.0, 10.0, "a", "a text", "a summary", []),
            RefinedSegment(1, 10.0, 20.0, "b", "b text", "b summary", []),
        ],
        "zh",
        20.0,
    )
    object.__setattr__(ctx.artifacts, "audio", SimpleNamespace(get_refined=lambda: refined))

    align.run(ctx)

    loaded = read_json_file(ctx.paths.visual_alignments_json, list[VisualAlignment])
    assert [item.segment_id for item in loaded] == [0, 1, 1]
    assert [item.frame_id for item in loaded] == [1, 2, 3]


def test_describe_uses_configured_parallelism(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    alignments = [
        VisualAlignment(0, 1, 1.0, Path("000001.png"), "ppt"),
        VisualAlignment(0, 2, 2.0, Path("000002.png"), "chart"),
    ]
    refined = RefinedTranscript([RefinedSegment(0, 0.0, 10.0, "主题", "正文", "摘要", [])], "zh", 10.0)
    object.__setattr__(ctx.artifacts, "audio", SimpleNamespace(is_complete=lambda: True, get_refined=lambda: refined, get_text_at=lambda start, end, strip_refs=True: "正文"))
    object.__setattr__(ctx.config.visual_pipeline.describe, "concurrent_calls", 5)
    (ctx.paths.visual_semantic_frames_dir / "000001.png").write_bytes(b"frame")
    (ctx.paths.visual_semantic_frames_dir / "000002.png").write_bytes(b"frame")
    template = tmp_path / "describe.jinja"
    template.write_text("prompt", encoding="utf-8")
    monkeypatch.setattr(describe, "read_alignments", lambda path: alignments)
    monkeypatch.setattr(describe, "prompt_path", lambda name: template)
    monkeypatch.setattr(describe, "hash_prompt_template", lambda path: "prompt")
    monkeypatch.setattr(describe, "cached_output", lambda *args, **kwargs: None)
    calls = []

    def run_parallel(items, worker, *, desc: str, unit: str, max_workers: int):  # type: ignore[no-untyped-def]
        calls.append((desc, unit, max_workers, len(items)))
        return [VisualDescription(item[0].segment_id, item[0].frame_id, 0.0, 10.0, item[0].image_source_path, item[0].medium, "中文描述") for item in items]

    monkeypatch.setattr(describe, "run_parallel", run_parallel)

    output = describe.run(ctx)

    assert output.metadata["item_count"] == 2
    assert calls == [("visual.describe", "alignment", 5, 2)]


def test_describe_uses_ref_stripped_audio_context(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    alignments = [VisualAlignment(0, 1, 1.0, Path("000001.png"), "ppt")]
    refined = RefinedTranscript([RefinedSegment(0, 0.0, 10.0, "主题", "正文 [[REF:0]]", "摘要", [0])], "zh", 10.0)
    audio_calls = []

    def get_text_at(start: float, end: float, strip_refs: bool = True):  # type: ignore[no-untyped-def]
        audio_calls.append((start, end, strip_refs))
        return "正文"

    object.__setattr__(ctx.artifacts, "audio", SimpleNamespace(is_complete=lambda: True, get_refined=lambda: refined, get_text_at=get_text_at))
    (ctx.paths.visual_semantic_frames_dir / "000001.png").write_bytes(b"frame")
    template = tmp_path / "describe.jinja"
    template.write_text("prompt", encoding="utf-8")
    monkeypatch.setattr(describe, "read_alignments", lambda path: alignments)
    monkeypatch.setattr(describe, "prompt_path", lambda name: template)
    monkeypatch.setattr(describe, "hash_prompt_template", lambda path: "prompt")
    monkeypatch.setattr(describe, "cached_output", lambda *args, **kwargs: None)
    captured_items = []

    def run_parallel(items, worker, *, desc: str, unit: str, max_workers: int):  # type: ignore[no-untyped-def]
        captured_items.extend(items)
        return [VisualDescription(item[0].segment_id, item[0].frame_id, 0.0, 10.0, item[0].image_source_path, item[0].medium, "中文描述") for item in items]

    monkeypatch.setattr(describe, "run_parallel", run_parallel)

    describe.run(ctx)

    assert audio_calls == [(0.0, 10.0, True)]
    assert captured_items == [(alignments[0], "正文")]


def test_describe_cache_key_includes_semantic_frame_content(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    alignments = [VisualAlignment(0, 1, 1.0, Path("000001.png"), "ppt")]
    refined = RefinedTranscript([RefinedSegment(0, 0.0, 10.0, "主题", "正文", "摘要", [])], "zh", 10.0)
    object.__setattr__(ctx.artifacts, "audio", SimpleNamespace(is_complete=lambda: True, get_refined=lambda: refined, get_text_at=lambda start, end, strip_refs=True: "正文"))
    atomic_write_json(ctx.paths.visual_alignments_json, alignments)
    (ctx.paths.visual_semantic_frames_dir / "000001.png").write_bytes(b"frame")
    calls = []

    def run_parallel(items, worker, *, desc: str, unit: str, max_workers: int):  # type: ignore[no-untyped-def]
        calls.append((items, desc, unit, max_workers))
        return [VisualDescription(item[0].segment_id, item[0].frame_id, 0.0, 10.0, item[0].image_source_path, item[0].medium, "中文描述") for item in items]

    monkeypatch.setattr(describe, "run_parallel", run_parallel)

    first = describe.run(ctx)
    second = describe.run(ctx)
    (ctx.paths.visual_semantic_frames_dir / "000001.png").write_bytes(b"changed")
    third = describe.run(ctx)

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert third.cache_hit is False
    assert len(calls) == 2


def test_describe_rejects_punctuation_only_description() -> None:
    assert describe._has_description_content("中文描述") is True
    assert describe._has_description_content("graphene") is True
    assert describe._has_description_content(":") is False


def test_describe_one_retries_empty_description(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    alignment = VisualAlignment(0, 1, 1.0, Path("000001.png"), "ppt")
    segment_by_id = {0: RefinedSegment(0, 0.0, 10.0, "主题", "正文", "摘要", [])}
    image_path = ctx.paths.visual_semantic_frames_dir / "000001.png"
    image_path.write_bytes(b"frame")
    template = tmp_path / "describe.jinja"
    template.write_text("prompt", encoding="utf-8")
    responses = iter([SimpleNamespace(description=":"), SimpleNamespace(description="中文描述")])
    monkeypatch.setattr(describe, "complete_json", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(describe, "for_task", lambda *args, **kwargs: object())

    result = describe._describe_one(ctx, template, segment_by_id, (alignment, "正文"))

    assert result.description == "中文描述"


def test_resolve_visual_image_path_rejects_escape(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "lecture.mp4", tmp_path / "cache", tmp_path / "output", "inputhash")

    assert resolve_visual_filter_image_path(paths, Path("000001.png")) == (paths.visual_filter_frames_dir / "000001.png").resolve()
    assert resolve_visual_semantic_image_path(paths, Path("000001.png")) == (paths.visual_semantic_frames_dir / "000001.png").resolve()
    with pytest.raises(ValueError, match="visual_filter_frames_dir"):
        resolve_visual_filter_image_path(paths, Path("../outside.png"))
    with pytest.raises(ValueError, match="visual_semantic_frames_dir"):
        resolve_visual_semantic_image_path(paths, Path("../outside.png"))


def _ctx(tmp_path: Path, mode: str = "audio_only") -> PipelineContext:
    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"source")
    paths = build_paths(source, tmp_path / "cache", tmp_path / "output", "inputhash")
    for directory in (paths.run_dir, paths.visual_dir, paths.visual_filter_frames_dir, paths.visual_semantic_frames_dir, paths.output_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return PipelineContext(source, "inputhash", mode, _config(tmp_path), paths, ArtifactBundle(audio=SimpleNamespace(), visual=SimpleNamespace()))


def _candidate(timestamp: float, kind: str) -> filter.CandidateFrame:
    return filter.CandidateFrame(timestamp, _test_image(kind))


def _detection(scenes: list[filter.SceneWindow], threshold: float = 10.0) -> filter.SceneDetection:
    return filter.SceneDetection(scenes, threshold)


def _test_image(kind: str):  # type: ignore[no-untyped-def]
    import cv2
    import numpy as np

    image = np.full((120, 180, 3), 255, dtype=np.uint8)
    if kind == "text":
        for index, text in enumerate(["AI Safety", "Threat Models", "Robustness"]):
            cv2.putText(image, text, (12, 32 + (index * 32)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2, cv2.LINE_AA)
    elif kind == "dark":
        image[:] = 8
    return image


def _write_filter_frames(ctx: PipelineContext, names: list[str]) -> None:
    for name in names:
        (ctx.paths.visual_filter_frames_dir / name).write_bytes(b"frame")


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "project": {"cache_dir": tmp_path / "cache", "output_dir": tmp_path / "output"},
            "llm": {
                "profiles": {
                    "main": {"provider": "openai_compatible_chat", "base_url": "http://localhost:8000/v1", "api_key_env": None, "model": "test"},
                    "vlm": {"provider": "openai_compatible_chat", "base_url": "http://localhost:8000/v1", "api_key_env": None, "model": "test", "capabilities": ["vision"]},
                }
            },
            "tasks": {"segment": "main", "refine": "main", "outline": "main", "section": "main", "slide_judge": "vlm", "slide_describe": "main"},
        }
    )
