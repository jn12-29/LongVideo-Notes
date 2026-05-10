from dataclasses import dataclass
import json
from pathlib import Path

import pytest

from lvnotes.core.cache import atomic_write_json, build_cache_key, hash_json, read_cache_manifest
from lvnotes.core.config import AppConfig
from lvnotes.core.context import ArtifactBundle, PipelineContext
from lvnotes.core.paths import build_paths, make_output_stem, make_timestamped_output_path
from lvnotes.core.serialization import from_jsonable, to_jsonable
from lvnotes.core.schemas import Transcript, TranscriptSegment, WordTimestamp
from lvnotes.core.schemas.merge import Chapter, ContentBlock, Outline, VisualSlot
from lvnotes.core.slugs import make_chapter_anchor
from lvnotes.core.timestamps import format_hms, format_mmss, parse_ts_marker, render_timestamp
from lvnotes.core.transcript import slice_transcript_text
from lvnotes.merge import assemble
from lvnotes.merge import unify
from lvnotes.merge.assemble import _normalize_markdown_spacing, _render_refs, _strip_section_heading
from lvnotes.merge import outline as outline_stage
from lvnotes.merge.outline import _validate_outline
from lvnotes.llm.types import LLMTextResult, LLMUsage


@dataclass(frozen=True)
class ExampleChild:
    path: Path


@dataclass(frozen=True)
class ExamplePayload:
    name: str
    children: list[ExampleChild]


def test_serialization_round_trip_dataclass_with_path() -> None:
    payload = ExamplePayload(name="demo", children=[ExampleChild(path=Path("frames/001.jpg"))])

    jsonable = to_jsonable(payload)
    restored = from_jsonable(ExamplePayload, jsonable)

    assert restored == payload


def test_hash_json_is_stable_for_key_order() -> None:
    assert hash_json({"b": 2, "a": 1}) == hash_json({"a": 1, "b": 2})
    assert build_cache_key("stage", {"input": "abc"}) == build_cache_key("stage", {"input": "abc"})


def test_set_serialization_is_sorted_for_stable_profile_hash() -> None:
    assert to_jsonable(frozenset({"vision", "json_mode", "reasoning"})) == [
        "json_mode",
        "reasoning",
        "vision",
    ]


def test_timestamp_formatting_and_marker_parsing() -> None:
    assert format_hms(3723.9) == "01:02:03"
    assert format_mmss(83.2) == "01:23"
    assert render_timestamp(83.2, "[{mmss}]") == "[01:23]"
    assert parse_ts_marker("[[TS:83.200]]") == 83.2


def test_invalid_timestamp_marker_raises() -> None:
    with pytest.raises(ValueError):
        parse_ts_marker("[[BAD:1]]")


def test_chapter_anchor_keeps_cjk_and_prefix() -> None:
    assert make_chapter_anchor(3, " 第一章: 概念 / Demo ").startswith("chapter-3-")
    assert "第一章" in make_chapter_anchor(3, " 第一章: 概念 / Demo ")


def test_output_stem_preserves_cjk_and_removes_unsafe_characters() -> None:
    assert make_output_stem(Path("20260420-金涌院士报告前10分钟音频.mp3")) == "20260420-金涌院士报告前10分钟音频"
    assert make_output_stem(Path(" Demo: A? (v1).mp4 ")) == "Demo-A-v1"
    assert make_output_stem(Path("???.mp4")) == "note"


def test_build_paths_uses_source_named_output_note() -> None:
    paths = build_paths(Path("/tmp/20260420-金涌院士报告前10分钟音频.mp3"), Path("cache"), Path("output"), "abc")

    assert paths.output_note_md == Path("output/20260420-金涌院士报告前10分钟音频.md")
    assert paths.debug_dir == Path("cache/abc/debug")
    assert make_timestamped_output_path(paths.output_note_md, "20260509-085423") == Path("output/20260420-金涌院士报告前10分钟音频-20260509-085423.md")


def test_build_paths_can_preserve_directory_input_relative_output_parent() -> None:
    paths = build_paths(Path("/tmp/course/week1/lecture.mp4"), Path("cache"), Path("output"), "abc", output_subdir=Path("week1"))

    assert paths.output_note_md == Path("output/week1/lecture.md")
    assert make_timestamped_output_path(paths.output_note_md, "20260509-085423") == Path("output/week1/lecture-20260509-085423.md")


def test_unify_ignores_visual_artifacts_in_audio_only_mode(tmp_path: Path) -> None:
    ctx = _unify_ctx(tmp_path, "audio_only", visual=type("Visual", (), {"get_descriptions": lambda self: (_ for _ in ()).throw(AssertionError("should not read visual"))})())

    assert unify._visual_descriptions(ctx) == []


def test_unify_requires_visual_artifacts_in_multimodal_mode(tmp_path: Path) -> None:
    ctx = _unify_ctx(tmp_path, "multimodal", visual=None)

    with pytest.raises(Exception, match="visual descriptions"):
        unify._visual_descriptions(ctx)


def test_unify_visual_hash_distinguishes_empty_multimodal_from_audio_only() -> None:
    assert unify._visual_hash("audio_only", []) == "audio_only"
    assert unify._visual_hash("multimodal", []) != "audio_only"


def _unify_ctx(tmp_path: Path, mode: str, visual: object | None) -> PipelineContext:
    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"source")
    paths = build_paths(source, tmp_path / "cache", tmp_path / "output", "inputhash")
    return PipelineContext(source, "inputhash", mode, _assemble_config(), paths, ArtifactBundle(audio=type("Audio", (), {})(), visual=visual))  # type: ignore[arg-type]


def test_assemble_writes_latest_and_timestamped_outputs(tmp_path: Path) -> None:
    ctx = _assemble_ctx(tmp_path)

    first = assemble.run(ctx)
    second = assemble.run(ctx)

    archived = [path for path in first.output_paths if path.name.startswith("讲座-202")]
    assert ctx.paths.output_note_md == tmp_path / "output" / "讲座.md"
    assert ctx.paths.output_note_md.exists()
    assert ctx.paths.cache_note_md.exists()
    assert archived and archived[0].exists()
    assert len(second.output_paths) == 3
    assert second.cache_hit is True
    assert second.output_paths[1].exists()
    manifest = read_cache_manifest(ctx.paths.cache_note_md.with_name("note.md.cache.json"))
    assert manifest.output_paths == [ctx.paths.output_note_md, ctx.paths.cache_note_md]


def test_assemble_writes_explicit_chapter_anchors(tmp_path: Path) -> None:
    ctx = _assemble_ctx(tmp_path)

    assemble.run(ctx)

    note = ctx.paths.output_note_md.read_text(encoding="utf-8")
    assert "- [开场](#chapter-1-开场)" in note
    assert '<a id="chapter-1-开场"></a>\n## 开场' in note


def test_assemble_writes_per_output_assets_and_rewrites_cache_links(tmp_path: Path) -> None:
    ctx = _assemble_ctx_with_visual(tmp_path)

    output = assemble.run(ctx)
    archived = [path for path in output.output_paths if path.name.startswith("讲座-202")][0]

    latest_note = ctx.paths.output_note_md.read_text(encoding="utf-8")
    archived_note = archived.read_text(encoding="utf-8")
    assert "../cache/" not in latest_note
    assert "../cache/" not in archived_note
    assert "讲座_assets/000001.png" in latest_note
    assert f"{archived.stem}_assets/000001.png" in archived_note
    assert "讲座_assets/000001.png" not in archived_note
    assert (ctx.paths.output_note_md.parent / "讲座_assets" / "000001.png").read_bytes() == b"image"
    assert (archived.parent / f"{archived.stem}_assets" / "000001.png").read_bytes() == b"image"


def test_assemble_cache_hit_restores_per_output_assets(tmp_path: Path) -> None:
    ctx = _assemble_ctx_with_visual(tmp_path)

    first = assemble.run(ctx)
    first_archived = [path for path in first.output_paths if path.name.startswith("讲座-202")][0]
    (ctx.paths.output_note_md.parent / "讲座_assets" / "000001.png").unlink()
    (first_archived.parent / f"{first_archived.stem}_assets" / "000001.png").unlink()

    second = assemble.run(ctx)
    second_archived = [path for path in second.output_paths if path.name.startswith("讲座-202")][0]

    assert second.cache_hit is True
    assert (ctx.paths.output_note_md.parent / "讲座_assets" / "000001.png").read_bytes() == b"image"
    assert (second_archived.parent / f"{second_archived.stem}_assets" / "000001.png").read_bytes() == b"image"


def test_assemble_cache_key_includes_mode(tmp_path: Path) -> None:
    ctx = _assemble_ctx(tmp_path)
    assemble.run(ctx)
    multimodal_ctx = PipelineContext(ctx.source_path, ctx.input_hash, "multimodal", ctx.config, ctx.paths, ctx.artifacts)

    output = assemble.run(multimodal_ctx)

    assert output.cache_hit is False
    assert "mode: multimodal" in ctx.paths.output_note_md.read_text(encoding="utf-8")


def _assemble_ctx(tmp_path: Path) -> PipelineContext:
    source = tmp_path / "讲座.mp3"
    source.write_bytes(b"audio")
    paths = build_paths(source, tmp_path / "cache", tmp_path / "output", "inputhash")
    for directory in (paths.run_dir, paths.sections_dir, paths.output_dir):
        directory.mkdir(parents=True, exist_ok=True)
    atomic_write_json(paths.outline_json, Outline([Chapter(1, "开场", "summary", 0, 0)]))
    atomic_write_json(paths.content_blocks_json, [ContentBlock(0, 0.0, 1.0, "topic", "正文。", "summary", [], [])])
    (paths.sections_dir / "001.md").write_text("[[TS:0.000]] 正文。\n", encoding="utf-8")
    return PipelineContext(source, "inputhash", "audio_only", _assemble_config(), paths, ArtifactBundle(audio=type("Audio", (), {"get_duration": lambda self: 1.0})()))


def _assemble_ctx_with_visual(tmp_path: Path) -> PipelineContext:
    source = tmp_path / "讲座.mp4"
    source.write_bytes(b"video")
    paths = build_paths(source, tmp_path / "cache", tmp_path / "output", "inputhash")
    for directory in (paths.run_dir, paths.sections_dir, paths.output_dir, paths.visual_semantic_frames_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (paths.visual_semantic_frames_dir / "000001.png").write_bytes(b"image")
    atomic_write_json(paths.outline_json, Outline([Chapter(1, "开场", "summary", 0, 0)]))
    atomic_write_json(
        paths.content_blocks_json,
        [ContentBlock(0, 0.0, 1.0, "topic", "正文。", "summary", [], [VisualSlot(Path("000001.png"), "desc", "ppt", 0.0, 1.0, 0)])],
    )
    legacy_image_path = Path("..") / "cache" / "inputhash" / "visual" / "semantic_frames" / "000001.png"
    (paths.sections_dir / "001.md").write_text(f"[[TS:0.000]] 正文。\n\n![desc]({legacy_image_path.as_posix()})\n", encoding="utf-8")
    return PipelineContext(source, "inputhash", "multimodal", _assemble_config(), paths, ArtifactBundle(audio=type("Audio", (), {"get_duration": lambda self: 1.0})()))


def _assemble_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "llm": {
                "profiles": {
                    "main": {"provider": "openai_compatible_chat", "base_url": "http://localhost:8000/v1", "api_key_env": None, "model": "test"},
                    "vlm": {"provider": "openai_compatible_chat", "base_url": "http://localhost:8000/v1", "api_key_env": None, "model": "test", "capabilities": ["vision"]},
                }
            },
            "tasks": {"segment": "main", "refine": "main", "outline": "main", "section": "main", "slide_judge": "vlm", "slide_describe": "main"},
        }
    )


def test_assemble_strips_generated_section_heading() -> None:
    assert _strip_section_heading("## 创新主题与论述\n\n正文", "创新主题与论述") == "正文"
    assert _strip_section_heading("# 3. 国际竞争与自主创新\n正文", "国际竞争与自主创新") == "正文"
    assert _strip_section_heading("## 3. 国际竞争与自主创新\n正文", "国际竞争与自主创新") == "正文"
    assert _strip_section_heading("### 3. 国际竞争与自主创新\n正文", "国际竞争与自主创新") == "### 3. 国际竞争与自主创新\n正文"
    assert _strip_section_heading("### 国际竞争与自主创新\n正文", "国际竞争与自主创新") == "正文"
    assert _strip_section_heading("### 内部小标题\n正文", "章节标题") == "### 内部小标题\n正文"
    assert _strip_section_heading("#### 更细小标题\n正文", "章节标题") == "#### 更细小标题\n正文"


def test_render_refs_links_current_chapter_refs() -> None:
    text = "当前 [[REF:2]]，前文 [[REF:0]]。"
    rendered = _render_refs(
        text,
        {0: 1, 2: 2},
        {1: "chapter-1-a", 2: "chapter-2-b"},
    )

    assert rendered == "当前 [§3](#chapter-2-b)，前文 [§1](#chapter-1-a)。"


def test_render_refs_falls_back_to_plain_text_for_unknown_blocks() -> None:
    rendered = _render_refs("未知 [[REF:99]]。", {}, {})

    assert rendered == "未知 §100。"


def test_normalize_markdown_spacing_cleans_ref_and_timestamp_spacing() -> None:
    assert _normalize_markdown_spacing("[00:05:32]那么是不是。 ") == "[00:05:32] 那么是不是。"
    assert _normalize_markdown_spacing("当前 ，前文。") == "当前，前文。"
    assert _normalize_markdown_spacing("[00:01:45]  \n正文  ") == "[00:01:45]\n正文"
    assert _normalize_markdown_spacing("结论[00:07:05] 。") == "[00:07:05] 结论。"
    assert _normalize_markdown_spacing("结论。[00:07:05]") == "[00:07:05] 结论。"
    assert _normalize_markdown_spacing("正文。[00:07:05](https://example.com/watch?t=425)") == "[00:07:05](https://example.com/watch?t=425) 正文。"
    assert _normalize_markdown_spacing("[00:01:00](u)正文") == "[00:01:00](u) 正文"
    assert _normalize_markdown_spacing("A。[00:01:00](u1) B。[00:02:00](u2)") == "A。[00:01:00](u1) B。[00:02:00](u2)"
    assert _normalize_markdown_spacing("### 小标题[00:07:05]") == "### 小标题[00:07:05]"
    assert _normalize_markdown_spacing("### 小标题[00:07:05]正文") == "### 小标题[00:07:05]正文"
    assert _normalize_markdown_spacing("### 小标题[00:07:05](https://example.com/watch?t=425)正文") == "### 小标题[00:07:05](https://example.com/watch?t=425)正文"


def test_render_timestamps_accepts_range_marker_start() -> None:
    class AssembleConfig:
        timestamp_format = "[{hms}]"
        video_url_template = None

    class MergeConfig:
        assemble = AssembleConfig()

    class Config:
        merge = MergeConfig()

    class Ctx:
        config = Config()

    from lvnotes.merge.assemble import _render_timestamps

    assert _render_timestamps(Ctx(), "[[TS:67.340-77.250]] 正文") == "[00:01:07] 正文"


def _outline(chapters: list[tuple[int, int, int]]) -> Outline:
    return Outline(
        chapters=[
            Chapter(id=chapter_id, title=f"Chapter {chapter_id}", summary="summary", block_id_start=start, block_id_end=end)
            for chapter_id, start, end in chapters
        ]
    )


def test_validate_outline_accepts_contiguous_coverage() -> None:
    _validate_outline(_outline([(1, 0, 1), (2, 2, 4)]), block_count=5)


def test_validate_outline_rejects_invalid_ids_and_ranges() -> None:
    with pytest.raises(Exception, match="id or range"):
        _validate_outline(_outline([(0, 0, 1)]), block_count=2)

    with pytest.raises(Exception, match="id or range"):
        _validate_outline(_outline([(1, 0, 1), (2, 3, 2), (3, 3, 4)]), block_count=5)


def test_validate_outline_rejects_missing_or_overlapping_blocks() -> None:
    with pytest.raises(Exception, match="coverage"):
        _validate_outline(_outline([(1, 1, 2)]), block_count=3)

    with pytest.raises(Exception, match="coverage"):
        _validate_outline(_outline([(1, 0, 1)]), block_count=3)

    with pytest.raises(Exception, match="contiguous"):
        _validate_outline(_outline([(1, 0, 1), (2, 3, 4)]), block_count=5)

    with pytest.raises(Exception, match="contiguous"):
        _validate_outline(_outline([(1, 0, 2), (2, 2, 4)]), block_count=5)


def test_outline_retries_invariant_failure_and_writes_debug(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _outline_ctx(tmp_path)
    invalid = _outline([(1, 0, 0)])
    valid = _outline([(1, 0, 1)])
    calls = []

    def complete_json_with_raw(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(args)
        outline = invalid if len(calls) == 1 else valid
        return outline, LLMTextResult(text=f"raw-{len(calls)}", model="test", usage=LLMUsage(None, None, None))

    monkeypatch.setattr(outline_stage, "for_task", lambda *args, **kwargs: object())
    monkeypatch.setattr(outline_stage, "complete_json_with_raw", complete_json_with_raw)

    output = outline_stage.run(ctx)

    debug_files = sorted(ctx.paths.debug_dir.glob("outline-failure-*.json"))
    debug_payload = json.loads(debug_files[0].read_text(encoding="utf-8"))
    assert len(calls) == 2
    assert len(debug_files) == 1
    assert output.output_paths == [ctx.paths.outline_json]
    assert debug_payload["stage"] == "merge.outline"
    assert debug_payload["attempt"] == 1
    assert debug_payload["raw_response"] == "raw-1"
    assert debug_payload["parsed_outline"]["chapters"][0]["block_id_end"] == 0
    assert json.loads(ctx.paths.outline_json.read_text(encoding="utf-8"))["chapters"][0]["block_id_end"] == 1


def test_outline_keeps_timestamped_debug_history_when_retries_fail(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx = _outline_ctx(tmp_path)
    calls = []

    def complete_json_with_raw(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(args)
        return _outline([(1, 0, 0)]), LLMTextResult(text=f"raw-{len(calls)}", model="test", usage=LLMUsage(None, None, None))

    monkeypatch.setattr(outline_stage, "for_task", lambda *args, **kwargs: object())
    monkeypatch.setattr(outline_stage, "complete_json_with_raw", complete_json_with_raw)

    with pytest.raises(Exception, match="coverage"):
        outline_stage.run(ctx)

    debug_files = sorted(ctx.paths.debug_dir.glob("outline-failure-*.json"))
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in debug_files]
    assert len(calls) == 2
    assert len(debug_files) == 2
    assert len({path.name for path in debug_files}) == 2
    assert [payload["attempt"] for payload in payloads] == [1, 2]
    assert [payload["raw_response"] for payload in payloads] == ["raw-1", "raw-2"]


def _outline_ctx(tmp_path: Path) -> PipelineContext:
    source = tmp_path / "讲座.mp3"
    source.write_bytes(b"audio")
    paths = build_paths(source, tmp_path / "cache", tmp_path / "output", "inputhash")
    for directory in (paths.run_dir, paths.debug_dir):
        directory.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        paths.content_blocks_json,
        [ContentBlock(0, 0.0, 1.0, "topic", "text", "summary", [], []), ContentBlock(1, 1.0, 2.0, "topic", "text", "summary", [], [])],
    )
    return PipelineContext(
        source,
        "inputhash",
        "audio_only",
        _assemble_config(),
        paths,
        ArtifactBundle(audio=type("Audio", (), {})()),
    )


def test_slice_transcript_text_uses_words_for_partial_segment() -> None:
    transcript = Transcript(
        segments=[
            TranscriptSegment(
                id=0,
                start=0.0,
                end=10.0,
                text="abcdef",
                words=[
                    WordTimestamp("ab", 0.0, 2.0, 1.0),
                    WordTimestamp("cd", 2.0, 5.0, 1.0),
                    WordTimestamp("ef", 5.0, 10.0, 1.0),
                ],
            )
        ],
        language="zh",
        duration=10.0,
    )

    assert slice_transcript_text(transcript, 0.0, 5.0) == "abcd"
    assert slice_transcript_text(transcript, 5.0, 10.0) == "ef"


def test_slice_transcript_text_allows_zero_duration_words() -> None:
    transcript = Transcript(
        segments=[
            TranscriptSegment(
                id=0,
                start=0.0,
                end=2.0,
                text="abc",
                words=[
                    WordTimestamp("a", 0.0, 0.0, 1.0),
                    WordTimestamp("b", 1.0, 1.0, 1.0),
                    WordTimestamp("c", 2.0, 2.0, 1.0),
                ],
            )
        ],
        language="zh",
        duration=2.0,
    )

    assert slice_transcript_text(transcript, 0.0, 1.5) == "ab"


def test_slice_transcript_text_preserves_word_spacing() -> None:
    transcript = Transcript(
        segments=[
            TranscriptSegment(
                id=0,
                start=0.0,
                end=2.0,
                text="hello world",
                words=[
                    WordTimestamp("hello", 0.0, 1.0, 1.0),
                    WordTimestamp(" world", 1.0, 2.0, 1.0),
                ],
            )
        ],
        language="en",
        duration=2.0,
    )

    assert slice_transcript_text(transcript, 0.0, 2.0) == "hello world"
