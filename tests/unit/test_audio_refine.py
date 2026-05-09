from pathlib import Path

import pytest

from lvnotes.audio_pipeline import refine
from lvnotes.core.cache import read_json_file
from lvnotes.core.config import AppConfig
from lvnotes.core.context import ArtifactBundle, PipelineContext
from lvnotes.core.exceptions import LLMError
from lvnotes.core.paths import build_paths
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.schemas import RefinedSegment, RefinedTranscript, SegmentList, SegmentMarker, Transcript, TranscriptSegment
from lvnotes.core.serialization import from_jsonable, to_jsonable


def _transcript() -> Transcript:
    return Transcript(
        segments=[
            TranscriptSegment(id=0, start=0.0, end=1.0, text="你好世界", words=[]),
            TranscriptSegment(id=1, start=1.0, end=2.0, text="继续讲解", words=[]),
        ],
        language="zh",
        duration=2.0,
    )


def _segments() -> SegmentList:
    return SegmentList(
        markers=[
            SegmentMarker(id=0, start=0.0, end=1.0, topic_hint="hello", boundary_reason="start"),
            SegmentMarker(id=1, start=1.0, end=2.0, topic_hint="continue", boundary_reason="next"),
        ]
    )


def _refined(segment_id: int) -> RefinedSegment:
    return RefinedSegment(
        id=segment_id,
        start=float(segment_id),
        end=float(segment_id + 1),
        topic=f"topic {segment_id}",
        cleaned_text=f"整理后的文本 {segment_id}。",
        summary=f"summary {segment_id}",
        cross_refs=[],
    )


def _config(mode: str = "adaptive", batch_size: int = 1) -> AppConfig:
    return AppConfig.model_validate(
        {
            "llm": {
                "profiles": {
                    "main": {
                        "provider": "openai_compatible_chat",
                        "base_url": "http://localhost:8000/v1",
                        "api_key_env": None,
                        "model": "test",
                        "capabilities": ["json_mode"],
                    },
                    "vlm": {
                        "provider": "openai_compatible_chat",
                        "base_url": "http://localhost:8000/v1",
                        "api_key_env": None,
                        "model": "test",
                        "capabilities": ["vision"],
                    },
                }
            },
            "tasks": {
                "segment": "main",
                "refine": "main",
                "outline": "main",
                "section": "main",
                "slide_judge": "vlm",
                "slide_describe": "main",
            },
            "audio_pipeline": {"refine": {"mode": mode, "batch_size": batch_size}},
        }
    )


def _ctx(tmp_path: Path, mode: str = "adaptive", batch_size: int = 1) -> PipelineContext:
    source = tmp_path / "input.wav"
    source.write_bytes(b"audio")
    paths = build_paths(source, tmp_path / "cache", tmp_path / "output", "inputhash")
    for directory in (paths.run_dir, paths.audio_dir, paths.refined_dir, paths.sections_dir, paths.output_dir):
        directory.mkdir(parents=True, exist_ok=True)
    artifacts = ArtifactBundle(audio=type("Audio", (), {})())
    return PipelineContext(source, "inputhash", "audio_only", _config(mode, batch_size), paths, artifacts)


def test_adaptive_refine_falls_back_to_batch_and_serial(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    transcript = _transcript()
    segments = _segments()

    def fail_single(*args: object) -> RefinedTranscript:
        raise LLMError("single failed")

    def fail_batch(*args: object) -> list[RefinedSegment]:
        raise LLMError("batch failed")

    monkeypatch.setattr(refine, "_run_single_call", fail_single)
    monkeypatch.setattr(refine, "_refine_batch", fail_batch)
    monkeypatch.setattr(refine, "_refine_one", lambda ctx, transcript, markers, marker: _refined(marker.id))

    result = refine._run_adaptive(ctx, transcript, segments)

    assert [segment.id for segment in result.segments] == [0, 1]
    assert read_json_file(ctx.paths.refined_dir / "0000.json", RefinedSegment).cleaned_text.endswith("。")
    assert read_json_file(ctx.paths.refined_dir / "0001.json", RefinedSegment).cleaned_text.endswith("。")


def test_adaptive_refine_falls_back_after_non_llm_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    transcript = _transcript()
    segments = _segments()

    monkeypatch.setattr(refine, "_run_single_call", lambda *args: (_ for _ in ()).throw(ValueError("single invalid")))
    monkeypatch.setattr(refine, "_refine_batch", lambda ctx, transcript, all_markers, batch_markers: [_refined(marker.id) for marker in batch_markers])

    result = refine._run_adaptive(ctx, transcript, segments)

    assert [segment.id for segment in result.segments] == [0, 1]


def test_adaptive_batch_falls_back_after_non_llm_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    transcript = _transcript()
    segments = _segments()

    monkeypatch.setattr(refine, "_refine_batch", lambda *args: (_ for _ in ()).throw(ValueError("batch invalid")))
    monkeypatch.setattr(refine, "_refine_one", lambda ctx, transcript, markers, marker: _refined(marker.id))

    result = refine._run_batched(ctx, transcript, segments, fallback_serial=True)

    assert [segment.id for segment in result.segments] == [0, 1]


def test_run_writes_segment_files_after_single_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, mode="single_call")
    transcript = _transcript()
    segments = _segments()
    ctx.artifacts.audio.get_transcript = lambda: transcript  # type: ignore[attr-defined]
    ctx.artifacts.audio.get_segments = lambda: segments  # type: ignore[attr-defined]
    result = RefinedTranscript(segments=[_refined(0), _refined(1)], language="zh", duration=2.0)
    monkeypatch.setattr(refine, "for_task", lambda *args, **kwargs: object())
    monkeypatch.setattr(refine, "complete_json", lambda *args, **kwargs: result)

    refine.run(ctx)

    assert ctx.paths.refined_transcript_json.exists()
    assert (ctx.paths.refined_dir / "0000.json").exists()
    assert (ctx.paths.refined_dir / "0001.json").exists()


def test_cache_hit_requires_segment_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, mode="single_call")
    transcript = _transcript()
    segments = _segments()
    ctx.artifacts.audio.get_transcript = lambda: transcript  # type: ignore[attr-defined]
    ctx.artifacts.audio.get_segments = lambda: segments  # type: ignore[attr-defined]
    result = RefinedTranscript(segments=[_refined(0), _refined(1)], language="zh", duration=2.0)
    monkeypatch.setattr(
        refine,
        "cached_output",
        lambda *args, **kwargs: StageOutput("refine", [ctx.paths.refined_transcript_json], True, "hash", {}),
    )
    called = {"value": False}

    def complete(*args: object, **kwargs: object) -> RefinedTranscript:
        called["value"] = True
        return result

    monkeypatch.setattr(refine, "for_task", lambda *args, **kwargs: object())
    monkeypatch.setattr(refine, "complete_json", complete)

    refine.run(ctx)

    assert called["value"] is True


def test_serial_resume_recomputes_from_invalid_partial(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, mode="serial")
    transcript = _transcript()
    segments = _segments()
    bad = RefinedSegment(0, 0.0, 1.0, "topic", "missing marker [[REF:1]]", "summary", [])
    refine.atomic_write_json(ctx.paths.refined_dir / "0000.json", bad)
    calls: list[int] = []

    def refine_one(ctx, transcript, markers, marker):  # type: ignore[no-untyped-def]
        calls.append(marker.id)
        return _refined(marker.id)

    monkeypatch.setattr(refine, "_refine_one", refine_one)

    result = refine._run_serial(ctx, transcript, segments.markers)

    assert calls == [0, 1]
    assert [segment.id for segment in result.segments] == [0, 1]


def test_serial_resume_recomputes_from_corrupt_partial(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, mode="serial")
    transcript = _transcript()
    segments = _segments()
    (ctx.paths.refined_dir / "0000.json").write_text("not json", encoding="utf-8")
    calls: list[int] = []

    def refine_one(ctx, transcript, markers, marker):  # type: ignore[no-untyped-def]
        calls.append(marker.id)
        return _refined(marker.id)

    monkeypatch.setattr(refine, "_refine_one", refine_one)

    result = refine._run_serial(ctx, transcript, segments.markers)

    assert calls == [0, 1]
    assert [segment.id for segment in result.segments] == [0, 1]


def test_cache_hit_requires_valid_segment_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, mode="single_call")
    transcript = _transcript()
    segments = _segments()
    ctx.artifacts.audio.get_transcript = lambda: transcript  # type: ignore[attr-defined]
    ctx.artifacts.audio.get_segments = lambda: segments  # type: ignore[attr-defined]
    (ctx.paths.refined_dir / "0000.json").write_text("not json", encoding="utf-8")
    refine.atomic_write_json(ctx.paths.refined_dir / "0001.json", _refined(1))
    result = RefinedTranscript(segments=[_refined(0), _refined(1)], language="zh", duration=2.0)
    monkeypatch.setattr(
        refine,
        "cached_output",
        lambda *args, **kwargs: StageOutput("refine", [ctx.paths.refined_transcript_json], True, "hash", {}),
    )
    called = {"value": False}

    def complete(*args: object, **kwargs: object) -> RefinedTranscript:
        called["value"] = True
        return result

    monkeypatch.setattr(refine, "for_task", lambda *args, **kwargs: object())
    monkeypatch.setattr(refine, "complete_json", complete)

    refine.run(ctx)

    assert called["value"] is True


def test_cache_hit_rejects_extra_segment_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, mode="single_call")
    transcript = _transcript()
    segments = _segments()
    ctx.artifacts.audio.get_transcript = lambda: transcript  # type: ignore[attr-defined]
    ctx.artifacts.audio.get_segments = lambda: segments  # type: ignore[attr-defined]
    refine.atomic_write_json(ctx.paths.refined_dir / "0000.json", _refined(0))
    refine.atomic_write_json(ctx.paths.refined_dir / "0001.json", _refined(1))
    refine.atomic_write_json(ctx.paths.refined_dir / "0002.json", RefinedSegment(2, 2.0, 3.0, "extra", "多余。", "extra", []))
    result = RefinedTranscript(segments=[_refined(0), _refined(1)], language="zh", duration=2.0)
    monkeypatch.setattr(
        refine,
        "cached_output",
        lambda *args, **kwargs: StageOutput("refine", [ctx.paths.refined_transcript_json], True, "hash", {}),
    )
    called = {"value": False}

    def complete(*args: object, **kwargs: object) -> RefinedTranscript:
        called["value"] = True
        return result

    monkeypatch.setattr(refine, "for_task", lambda *args, **kwargs: object())
    monkeypatch.setattr(refine, "complete_json", complete)

    refine.run(ctx)

    assert called["value"] is True


def test_cache_hit_requires_valid_refined_transcript(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, mode="single_call")
    transcript = _transcript()
    segments = _segments()
    ctx.artifacts.audio.get_transcript = lambda: transcript  # type: ignore[attr-defined]
    ctx.artifacts.audio.get_segments = lambda: segments  # type: ignore[attr-defined]
    refine.atomic_write_json(ctx.paths.refined_dir / "0000.json", _refined(0))
    refine.atomic_write_json(ctx.paths.refined_dir / "0001.json", _refined(1))
    ctx.paths.refined_transcript_json.write_text("not json", encoding="utf-8")
    result = RefinedTranscript(segments=[_refined(0), _refined(1)], language="zh", duration=2.0)
    monkeypatch.setattr(
        refine,
        "cached_output",
        lambda *args, **kwargs: StageOutput("refine", [ctx.paths.refined_transcript_json], True, "hash", {}),
    )
    called = {"value": False}

    def complete(*args: object, **kwargs: object) -> RefinedTranscript:
        called["value"] = True
        return result

    monkeypatch.setattr(refine, "for_task", lambda *args, **kwargs: object())
    monkeypatch.setattr(refine, "complete_json", complete)

    refine.run(ctx)

    assert called["value"] is True


def test_validate_refined_transcript_rejects_missing_segment() -> None:
    transcript = _transcript()
    segments = _segments()
    result = RefinedTranscript(segments=[_refined(0)], language="zh", duration=2.0)

    with pytest.raises(LLMError, match="segment count"):
        refine._validate_refined_transcript(result, transcript, segments.markers)


def test_refined_segment_list_serialization_round_trip() -> None:
    from lvnotes.core.schemas import RefinedSegmentList

    payload = RefinedSegmentList(segments=[_refined(0), _refined(1)])

    restored = from_jsonable(RefinedSegmentList, to_jsonable(payload))

    assert restored == payload


def test_refine_prompts_require_punctuation() -> None:
    prompt_texts = [
        refine.prompt_path("refine.jinja").read_text(encoding="utf-8"),
        refine.prompt_path("refine_single.jinja").read_text(encoding="utf-8"),
        refine.prompt_path("refine_batch.jinja").read_text(encoding="utf-8"),
    ]

    assert all("proper Chinese punctuation" in text for text in prompt_texts)
    assert all("Chinese commas" in text for text in prompt_texts)
    assert all("Do not output raw ASR-style text without punctuation" in text for text in prompt_texts)
