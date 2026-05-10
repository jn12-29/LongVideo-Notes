from pathlib import Path
from types import SimpleNamespace

import pytest

from lvnotes.core.cache import read_json_file
from lvnotes.core.config import AppConfig
from lvnotes.core.context import ArtifactBundle, PipelineContext
from lvnotes.core.exceptions import LLMError, MediaError
from lvnotes.core.paths import build_paths, resolve_visual_filter_image_path, resolve_visual_raw_image_path, resolve_visual_semantic_image_path
from lvnotes.core.schemas import RefinedSegment, RefinedTranscript, SampledFrame, VisualAlignment, VisualDescription, VisualSemanticJudgement, VisualSemanticJudgementList
from lvnotes.media import video
from lvnotes.visual_pipeline import align, describe, filter, sample, semantic_filter


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


def test_sample_rejects_empty_frame_result(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    monkeypatch.setattr(sample, "hash_file", lambda path: "inputhash")
    monkeypatch.setattr(sample, "extract_frames", lambda *args: [])

    with pytest.raises(MediaError, match="did not produce frames"):
        sample.run(ctx)


def test_sample_writes_raw_frame_paths(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    frame_path = ctx.paths.visual_raw_frames_dir / "000001.png"
    frame_path.write_bytes(b"frame")
    monkeypatch.setattr(sample, "hash_file", lambda path: "inputhash")
    monkeypatch.setattr(sample, "extract_frames", lambda *args: [SimpleNamespace(timestamp=0.0, path=frame_path)])

    sample.run(ctx)

    index = sample.read_samples(ctx.paths.visual_sample_json)
    assert index.frames[0].image_source_path == Path("000001.png")


def test_filter_copies_kept_frames_to_filter_frames(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    raw_frame = ctx.paths.visual_raw_frames_dir / "000001.png"
    raw_frame.write_bytes(b"frame")
    sample.atomic_write_json(ctx.paths.visual_sample_json, sample.VisualSampleIndex([SampledFrame(7, 1.0, Path("000001.png"))], 2.0))
    monkeypatch.setattr(filter, "_frame_signature", lambda path, crop: (1, [1.0], [0.0]))

    filter.run(ctx)

    filtered = filter.read_samples(ctx.paths.visual_filtered_sample_json)
    assert filtered.frames == [SampledFrame(7, 1.0, Path("000001.png"))]
    assert (ctx.paths.visual_filter_frames_dir / "000001.png").read_bytes() == b"frame"
    assert (ctx.paths.visual_filter_variants_dir / "default-p8-h0.12-dupP2-dupH0.03-dupPix0.02" / "frames" / "000001.png").read_bytes() == b"frame"
    assert (ctx.paths.visual_filter_variants_dir / "summary.json").exists()


def test_filter_syncs_active_variant_to_stable_outputs(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    object.__setattr__(ctx.config.visual_pipeline.filter, "active_variant", "loose")
    object.__setattr__(ctx.config, "filter_variants", _variant_file_config())
    _write_raw_frames(ctx, ["000001.png", "000002.png", "000003.png"])
    sample.atomic_write_json(
        ctx.paths.visual_sample_json,
        sample.VisualSampleIndex(
            [SampledFrame(1, 0.0, Path("000001.png")), SampledFrame(2, 1.0, Path("000002.png")), SampledFrame(3, 2.0, Path("000003.png"))],
            3.0,
        ),
    )
    monkeypatch.setattr(
        filter,
        "_frame_signature",
        lambda path, crop: {
            "000001.png": (0b0000, [1.0, 0.0], [0.0]),
            "000002.png": (0b1111, [0.0, 1.0], [1.0]),
            "000003.png": (0b0000, [0.99, 0.01], [0.01]),
        }[path.name],
    )

    filter.run(ctx)

    filtered = filter.read_samples(ctx.paths.visual_filtered_sample_json)
    assert [frame.id for frame in filtered.frames] == [1, 2, 3]
    assert (ctx.paths.visual_filter_variants_dir / "strict-p8-h0.12-dupP2-dupH0.03-dupPix0.02" / "frames" / "000003.png").exists() is False
    assert (ctx.paths.visual_filter_variants_dir / "loose-p8-h0.12-dupP2-dupH0.03-dupPix0.001" / "frames" / "000003.png").exists()
    assert (ctx.paths.visual_filter_frames_dir / "000003.png").exists()


def test_filter_no_cache_rebuilds_variants_and_stable_outputs(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    object.__setattr__(ctx, "no_cache", True)
    stale_variant = ctx.paths.visual_filter_variants_dir / "old" / "frames"
    stale_variant.mkdir(parents=True)
    (stale_variant / "stale.png").write_bytes(b"old")
    (ctx.paths.visual_filter_frames_dir / "stale.png").write_bytes(b"old")
    _write_raw_frames(ctx, ["000001.png"])
    sample.atomic_write_json(ctx.paths.visual_sample_json, sample.VisualSampleIndex([SampledFrame(1, 0.0, Path("000001.png"))], 1.0))
    monkeypatch.setattr(filter, "_frame_signature", lambda path, crop: (1, [1.0], [0.0]))

    filter.run(ctx)

    assert not (ctx.paths.visual_filter_variants_dir / "old").exists()
    assert not (ctx.paths.visual_filter_frames_dir / "stale.png").exists()
    assert (ctx.paths.visual_filter_frames_dir / "000001.png").exists()


def test_filter_cache_hit_rebuilds_missing_variants_without_rewriting_stable(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    _write_raw_frames(ctx, ["000001.png"])
    sample.atomic_write_json(ctx.paths.visual_sample_json, sample.VisualSampleIndex([SampledFrame(1, 0.0, Path("000001.png"))], 1.0))
    monkeypatch.setattr(filter, "_frame_signature", lambda path, crop: (1, [1.0], [0.0]))

    filter.run(ctx)
    (ctx.paths.visual_filter_variants_dir / "summary.json").unlink()
    stable = ctx.paths.visual_filter_frames_dir / "000001.png"
    stable.write_bytes(b"stable")

    output = filter.run(ctx)

    assert output.cache_hit is True
    assert stable.read_bytes() == b"stable"
    assert (ctx.paths.visual_filter_variants_dir / "summary.json").exists()


def test_filter_cache_hit_rebuilds_incomplete_variants_without_rewriting_stable(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    _write_raw_frames(ctx, ["000001.png"])
    sample.atomic_write_json(ctx.paths.visual_sample_json, sample.VisualSampleIndex([SampledFrame(1, 0.0, Path("000001.png"))], 1.0))
    monkeypatch.setattr(filter, "_frame_signature", lambda path, crop: (1, [1.0], [0.0]))

    filter.run(ctx)
    variant_frame = ctx.paths.visual_filter_variants_dir / "default-p8-h0.12-dupP2-dupH0.03-dupPix0.02" / "frames" / "000001.png"
    variant_frame.unlink()
    stable = ctx.paths.visual_filter_frames_dir / "000001.png"
    stable.write_bytes(b"stable")

    output = filter.run(ctx)

    assert output.cache_hit is True
    assert stable.read_bytes() == b"stable"
    assert variant_frame.exists()


def test_filter_does_not_keep_static_frames_by_default(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    _write_raw_frames(ctx, ["000001.png", "000121.png"])
    sample.atomic_write_json(
        ctx.paths.visual_sample_json,
        sample.VisualSampleIndex([SampledFrame(1, 0.0, Path("000001.png")), SampledFrame(121, 120.0, Path("000121.png"))], 121.0),
    )
    monkeypatch.setattr(filter, "_frame_signature", lambda path, crop: (1, [1.0], [0.0]))

    filter.run(ctx)

    filtered = filter.read_samples(ctx.paths.visual_filtered_sample_json)
    assert [frame.id for frame in filtered.frames] == [1]


def test_filter_keeps_static_frames_when_interval_is_configured(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    object.__setattr__(ctx.config.visual_pipeline.filter, "max_static_seconds", 60.0)
    _write_raw_frames(ctx, ["000001.png", "000121.png"])
    sample.atomic_write_json(
        ctx.paths.visual_sample_json,
        sample.VisualSampleIndex([SampledFrame(1, 0.0, Path("000001.png")), SampledFrame(121, 120.0, Path("000121.png"))], 121.0),
    )
    signatures = {
        "000001.png": (1, [1.0, 0.0], [0.0]),
        "000121.png": (1, [1.0, 0.0], [0.1]),
    }
    monkeypatch.setattr(filter, "_frame_signature", lambda path, crop: signatures[path.name])

    filter.run(ctx)

    filtered = filter.read_samples(ctx.paths.visual_filtered_sample_json)
    assert [frame.id for frame in filtered.frames] == [1, 121]


def test_filter_drops_non_adjacent_global_duplicates(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    _write_raw_frames(ctx, ["000001.png", "000002.png", "000003.png"])
    sample.atomic_write_json(
        ctx.paths.visual_sample_json,
        sample.VisualSampleIndex(
            [SampledFrame(1, 0.0, Path("000001.png")), SampledFrame(2, 1.0, Path("000002.png")), SampledFrame(3, 2.0, Path("000003.png"))],
            3.0,
        ),
    )
    signatures = {
        "000001.png": (0b0000, [1.0, 0.0], [0.0, 0.0]),
        "000002.png": (0b1111, [0.0, 1.0], [1.0, 1.0]),
        "000003.png": (0b0000, [0.99, 0.01], [0.01, 0.01]),
    }
    monkeypatch.setattr(filter, "_frame_signature", lambda path, crop: signatures[path.name])

    filter.run(ctx)

    filtered = filter.read_samples(ctx.paths.visual_filtered_sample_json)
    assert [frame.id for frame in filtered.frames] == [1, 2]


def test_filter_keeps_similar_layout_when_pixels_differ(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    _write_raw_frames(ctx, ["000001.png", "000002.png", "000003.png"])
    sample.atomic_write_json(
        ctx.paths.visual_sample_json,
        sample.VisualSampleIndex(
            [SampledFrame(1, 0.0, Path("000001.png")), SampledFrame(2, 1.0, Path("000002.png")), SampledFrame(3, 2.0, Path("000003.png"))],
            3.0,
        ),
    )
    signatures = {
        "000001.png": (0b0000, [1.0, 0.0], [0.0, 0.0]),
        "000002.png": (0b1111, [0.0, 1.0], [1.0, 1.0]),
        "000003.png": (0b0000, [0.99, 0.01], [0.10, 0.10]),
    }
    monkeypatch.setattr(filter, "_frame_signature", lambda path, crop: signatures[path.name])

    filter.run(ctx)

    filtered = filter.read_samples(ctx.paths.visual_filtered_sample_json)
    assert [frame.id for frame in filtered.frames] == [1, 2, 3]


def test_semantic_filter_copies_meaningful_frames(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _ctx(tmp_path, mode="multimodal")
    _write_filter_frames(ctx, ["000001.png", "000002.png"])
    sample.atomic_write_json(
        ctx.paths.visual_filtered_sample_json,
        sample.VisualSampleIndex([SampledFrame(1, 1.0, Path("000001.png")), SampledFrame(2, 2.0, Path("000002.png"))], 3.0),
    )
    monkeypatch.setattr(
        semantic_filter,
        "_judge_frames",
        lambda ctx, samples, template: VisualSemanticJudgementList(
            [VisualSemanticJudgement(1, "ppt", True, "content slide"), VisualSemanticJudgement(2, "speaker", False, "speaker only")]
        ),
    )

    semantic_filter.run(ctx)

    semantic = sample.read_samples(ctx.paths.visual_semantic_sample_json)
    assert semantic.frames == [SampledFrame(1, 1.0, Path("000001.png"))]
    assert (ctx.paths.visual_semantic_frames_dir / "000001.png").read_bytes() == b"frame"
    assert not (ctx.paths.visual_semantic_frames_dir / "000002.png").exists()


def test_semantic_filter_validates_exactly_one_judgement_per_frame() -> None:
    semantic_filter._validate_judgements(VisualSemanticJudgementList([VisualSemanticJudgement(1, "ppt", True, "ok")]), {1})
    with pytest.raises(LLMError):
        semantic_filter._validate_judgements(VisualSemanticJudgementList([VisualSemanticJudgement(1, "invalid", True, "ok")]), {1})
    with pytest.raises(LLMError):
        semantic_filter._validate_judgements(VisualSemanticJudgementList([VisualSemanticJudgement(1, "ppt", True, "ok")]), {1, 2})


def test_align_maps_semantic_frames_to_refined_segments(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, mode="multimodal")
    sample.atomic_write_json(
        ctx.paths.visual_semantic_sample_json,
        sample.VisualSampleIndex([SampledFrame(1, 1.0, Path("000001.png")), SampledFrame(2, 12.0, Path("000002.png")), SampledFrame(3, 25.0, Path("000003.png"))], 30.0),
    )
    sample.atomic_write_json(
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

    assert resolve_visual_raw_image_path(paths, Path("000001.png")) == (paths.visual_raw_frames_dir / "000001.png").resolve()
    assert resolve_visual_filter_image_path(paths, Path("000001.png")) == (paths.visual_filter_frames_dir / "000001.png").resolve()
    assert resolve_visual_semantic_image_path(paths, Path("000001.png")) == (paths.visual_semantic_frames_dir / "000001.png").resolve()
    with pytest.raises(ValueError, match="visual_raw_frames_dir"):
        resolve_visual_raw_image_path(paths, Path("../outside.png"))
    with pytest.raises(ValueError, match="visual_filter_frames_dir"):
        resolve_visual_filter_image_path(paths, Path("../outside.png"))
    with pytest.raises(ValueError, match="visual_semantic_frames_dir"):
        resolve_visual_semantic_image_path(paths, Path("../outside.png"))


def _ctx(tmp_path: Path, mode: str = "audio_only") -> PipelineContext:
    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"source")
    paths = build_paths(source, tmp_path / "cache", tmp_path / "output", "inputhash")
    for directory in (paths.run_dir, paths.visual_dir, paths.visual_raw_frames_dir, paths.visual_filter_frames_dir, paths.visual_filter_variants_dir, paths.visual_semantic_frames_dir, paths.output_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return PipelineContext(source, "inputhash", mode, _config(tmp_path), paths, ArtifactBundle(audio=SimpleNamespace(), visual=SimpleNamespace()))


def _write_raw_frames(ctx: PipelineContext, names: list[str]) -> None:
    for name in names:
        (ctx.paths.visual_raw_frames_dir / name).write_bytes(b"frame")


def _write_filter_frames(ctx: PipelineContext, names: list[str]) -> None:
    for name in names:
        (ctx.paths.visual_filter_frames_dir / name).write_bytes(b"frame")


def _variant_file_config():  # type: ignore[no-untyped-def]
    from lvnotes.core.config import VisualFilterVariantFileConfig

    return VisualFilterVariantFileConfig.model_validate(
        {
            "variants": [
                {"name": "strict", "duplicate_pixel_threshold": 0.02},
                {"name": "loose", "duplicate_pixel_threshold": 0.001},
            ]
        }
    )


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
