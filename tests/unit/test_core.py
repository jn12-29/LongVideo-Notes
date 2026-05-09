from dataclasses import dataclass
from pathlib import Path

import pytest

from lvnotes.core.cache import build_cache_key, hash_json
from lvnotes.core.serialization import from_jsonable, to_jsonable
from lvnotes.core.schemas import Transcript, TranscriptSegment, WordTimestamp
from lvnotes.core.schemas.merge import Chapter, Outline
from lvnotes.core.slugs import make_chapter_anchor
from lvnotes.core.timestamps import format_hms, format_mmss, parse_ts_marker, render_timestamp
from lvnotes.core.transcript import slice_transcript_text
from lvnotes.merge.assemble import _normalize_markdown_spacing, _render_refs, _strip_section_heading
from lvnotes.merge.outline import _validate_outline


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


def test_assemble_strips_generated_section_heading() -> None:
    assert _strip_section_heading("## 创新主题与论述\n\n正文", "创新主题与论述") == "正文"
    assert _strip_section_heading("# 3. 国际竞争与自主创新\n正文", "国际竞争与自主创新") == "正文"
    assert _strip_section_heading("## 3. 国际竞争与自主创新\n正文", "国际竞争与自主创新") == "正文"
    assert _strip_section_heading("### 3. 国际竞争与自主创新\n正文", "国际竞争与自主创新") == "### 3. 国际竞争与自主创新\n正文"
    assert _strip_section_heading("### 国际竞争与自主创新\n正文", "国际竞争与自主创新") == "正文"
    assert _strip_section_heading("### 内部小标题\n正文", "章节标题") == "### 内部小标题\n正文"
    assert _strip_section_heading("#### 更细小标题\n正文", "章节标题") == "#### 更细小标题\n正文"


def test_render_refs_drops_current_chapter_self_refs() -> None:
    text = "当前 [[REF:2]]，前文 [[REF:0]]。"
    rendered = _render_refs(
        text,
        {0: 1, 2: 2},
        {1: "chapter-1-a", 2: "chapter-2-b"},
        current_chapter_id=2,
    )

    assert rendered == "当前 ，前文 [§1](#chapter-1-a)。"


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
