from dataclasses import dataclass
from pathlib import Path

import pytest

from lvnotes.core.cache import build_cache_key, hash_json
from lvnotes.core.serialization import from_jsonable, to_jsonable
from lvnotes.core.schemas import Transcript, TranscriptSegment, WordTimestamp
from lvnotes.core.slugs import make_chapter_anchor
from lvnotes.core.timestamps import format_hms, format_mmss, parse_ts_marker, render_timestamp
from lvnotes.core.transcript import slice_transcript_text


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
