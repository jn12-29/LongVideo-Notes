from dataclasses import dataclass
from pathlib import Path

import pytest

from lvnotes.core.cache import build_cache_key, hash_json
from lvnotes.core.serialization import from_jsonable, to_jsonable
from lvnotes.core.slugs import make_chapter_anchor
from lvnotes.core.timestamps import format_hms, format_mmss, parse_ts_marker, render_timestamp


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
