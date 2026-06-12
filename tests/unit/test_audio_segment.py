import logging
from pathlib import Path

import pytest

from lvnotes.audio_pipeline import segment as segment_stage
from lvnotes.audio_pipeline.segment import (
    _cached_segments_valid,
    _snap_segments_to_transcript_timestamps,
    _transcript_lines,
    _validate_segments,
    _validate_segments_cover_transcript_text,
)
from lvnotes.core.cache import atomic_write_json, read_json_file
from lvnotes.core.config import AppConfig
from lvnotes.core.context import ArtifactBundle, PipelineContext
from lvnotes.core.exceptions import LLMError
from lvnotes.core.paths import build_paths
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.schemas import SegmentList, SegmentMarker, Transcript, TranscriptSegment, WordTimestamp


def _config() -> AppConfig:
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
        }
    )


def test_transcript_lines_expose_each_word_timestamp_within_asr_segment() -> None:
    transcript = Transcript(
        segments=[
            TranscriptSegment(
                id=0,
                start=0.0,
                end=25.0,
                text="abcdef",
                words=[
                    WordTimestamp("a", 0.0, 1.0, 1.0),
                    WordTimestamp("b", 5.0, 6.0, 1.0),
                    WordTimestamp("c", 10.0, 11.0, 1.0),
                    WordTimestamp("d", 15.0, 16.0, 1.0),
                    WordTimestamp("e", 20.0, 21.0, 1.0),
                    WordTimestamp("f", 24.0, 25.0, 1.0),
                ],
            )
        ],
        language="zh",
        duration=25.0,
    )

    assert _transcript_lines(transcript) == [
        "[0.000-1.000] a",
        "[5.000-6.000] b",
        "[10.000-11.000] c",
        "[15.000-16.000] d",
        "[20.000-21.000] e",
        "[24.000-25.000] f",
    ]


def test_transcript_lines_fallback_to_asr_segment_without_words() -> None:
    transcript = Transcript(
        segments=[TranscriptSegment(id=0, start=0.0, end=5.0, text="hello", words=[])],
        language="en",
        duration=5.0,
    )

    assert _transcript_lines(transcript) == ["[0.000-5.000] hello"]


def test_snap_segments_to_nearest_word_timestamps() -> None:
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
    segments = SegmentList(
        markers=[
            SegmentMarker(0, 0.01, 4.95, "first", "near word boundaries"),
            SegmentMarker(1, 5.05, 9.98, "second", "near word boundaries"),
        ]
    )

    snapped = _snap_segments_to_transcript_timestamps(segments, transcript)

    assert snapped.markers[0].start == 0.0
    assert snapped.markers[0].end == 5.0
    assert snapped.markers[1].start == 5.0
    assert snapped.markers[1].end == 10.0


def test_snap_segments_does_not_use_synthetic_duration_boundaries() -> None:
    transcript = Transcript(
        segments=[
            TranscriptSegment(
                id=0,
                start=0.0,
                end=10.0,
                text="hello",
                words=[WordTimestamp("hello", 1.0, 9.0, 1.0)],
            )
        ],
        language="en",
        duration=10.0,
    )
    segments = SegmentList(markers=[SegmentMarker(0, 0.9, 9.1, "only", "near word edges")])

    snapped = _snap_segments_to_transcript_timestamps(segments, transcript)

    assert snapped.markers[0].start == 1.0
    assert snapped.markers[0].end == 9.0


def test_snap_segments_warns_when_boundary_is_far_from_timestamp(caplog: pytest.LogCaptureFixture) -> None:
    transcript = Transcript(
        segments=[
            TranscriptSegment(
                id=0,
                start=0.0,
                end=10.0,
                text="hello world",
                words=[
                    WordTimestamp("hello", 0.0, 1.0, 1.0),
                    WordTimestamp(" world", 1.0, 10.0, 1.0),
                ],
            )
        ],
        language="en",
        duration=10.0,
    )
    segments = SegmentList(
        markers=[
            SegmentMarker(0, 0.0, 0.7, "first", "far from candidate"),
        ]
    )

    with caplog.at_level(logging.WARNING):
        snapped = _snap_segments_to_transcript_timestamps(segments, transcript)

    assert snapped.markers[0].end == 1.0
    assert "segment 0 end snapped from 0.700 to 1.000" in caplog.text


def test_snap_segments_does_not_warn_at_warning_threshold(caplog: pytest.LogCaptureFixture) -> None:
    transcript = Transcript(
        segments=[
            TranscriptSegment(
                id=0,
                start=0.0,
                end=10.0,
                text="hello world",
                words=[
                    WordTimestamp("hello", 0.0, 1.0, 1.0),
                    WordTimestamp(" world", 1.0, 10.0, 1.0),
                ],
            )
        ],
        language="en",
        duration=10.0,
    )
    segments = SegmentList(
        markers=[
            SegmentMarker(0, 0.0, 0.8, "first", "at threshold"),
        ]
    )

    with caplog.at_level(logging.WARNING):
        snapped = _snap_segments_to_transcript_timestamps(segments, transcript)

    assert snapped.markers[0].end == 1.0
    assert caplog.text == ""


def test_snap_segments_warns_and_snaps_when_boundary_is_very_far_from_timestamp(caplog: pytest.LogCaptureFixture) -> None:
    transcript = Transcript(
        segments=[TranscriptSegment(id=0, start=0.0, end=10.0, text="hello", words=[])],
        language="en",
        duration=10.0,
    )
    segments = SegmentList(
        markers=[
            SegmentMarker(0, 0.0, 5.0, "first", "too far"),
        ]
    )

    with caplog.at_level(logging.WARNING):
        snapped = _snap_segments_to_transcript_timestamps(segments, transcript)

    _validate_segments(snapped, transcript.duration)
    assert snapped.markers[0].end == 10.0
    assert "segment 0 end snapped from 5.000 to 10.000" in caplog.text


def test_snap_segments_snaps_boundaries_without_failure_threshold() -> None:
    transcript = Transcript(
        segments=[TranscriptSegment(id=0, start=0.0, end=10.0, text="hello", words=[])],
        language="en",
        duration=10.0,
    )
    segments = SegmentList(
        markers=[SegmentMarker(0, 2.0, 10.0, "first", "at threshold")]
    )

    snapped = _snap_segments_to_transcript_timestamps(segments, transcript)

    assert snapped.markers[0].start == 0.0
    assert snapped.markers[0].end == 10.0


def test_validate_segments_allows_gaps_between_word_edges() -> None:
    segments = SegmentList(
        markers=[
            SegmentMarker(0, 0.0, 4.0, "first", "ends at last word"),
            SegmentMarker(1, 5.0, 10.0, "second", "starts at first word"),
        ]
    )

    _validate_segments(segments, 10.0)


def test_validate_segments_cover_transcript_text_allows_silent_gap() -> None:
    transcript = Transcript(
        segments=[
            TranscriptSegment(
                id=0,
                start=0.0,
                end=10.0,
                text="ab",
                words=[
                    WordTimestamp("a", 0.0, 4.0, 1.0),
                    WordTimestamp("b", 5.0, 10.0, 1.0),
                ],
            )
        ],
        language="en",
        duration=10.0,
    )
    segments = SegmentList(
        markers=[
            SegmentMarker(0, 0.0, 4.0, "first", "first word"),
            SegmentMarker(1, 5.0, 10.0, "second", "second word"),
        ]
    )

    _validate_segments_cover_transcript_text(segments, transcript)


def test_validate_segments_cover_transcript_text_rejects_uncovered_word() -> None:
    transcript = Transcript(
        segments=[
            TranscriptSegment(
                id=0,
                start=0.0,
                end=10.0,
                text="abc",
                words=[
                    WordTimestamp("a", 0.0, 3.0, 1.0),
                    WordTimestamp("b", 4.0, 6.0, 1.0),
                    WordTimestamp("c", 7.0, 10.0, 1.0),
                ],
            )
        ],
        language="en",
        duration=10.0,
    )
    segments = SegmentList(
        markers=[
            SegmentMarker(0, 0.0, 3.0, "first", "first word"),
            SegmentMarker(1, 7.0, 10.0, "third", "third word"),
        ]
    )

    with pytest.raises(LLMError, match="uncovered transcript word"):
        _validate_segments_cover_transcript_text(segments, transcript)


def test_validate_segments_cover_transcript_text_rejects_uncovered_line_segment() -> None:
    transcript = Transcript(
        segments=[
            TranscriptSegment(id=0, start=0.0, end=3.0, text="first", words=[]),
            TranscriptSegment(id=1, start=4.0, end=6.0, text="missing", words=[]),
            TranscriptSegment(id=2, start=7.0, end=10.0, text="last", words=[]),
        ],
        language="en",
        duration=10.0,
    )
    segments = SegmentList(
        markers=[
            SegmentMarker(0, 0.0, 3.0, "first", "first line"),
            SegmentMarker(1, 7.0, 10.0, "last", "last line"),
        ]
    )

    with pytest.raises(LLMError, match="uncovered transcript segment"):
        _validate_segments_cover_transcript_text(segments, transcript)


def test_validate_segments_cover_transcript_text_rejects_partially_covered_line_segment() -> None:
    transcript = Transcript(
        segments=[TranscriptSegment(id=0, start=0.0, end=10.0, text="line-level text", words=[])],
        language="en",
        duration=10.0,
    )
    segments = SegmentList(
        markers=[
            SegmentMarker(0, 0.0, 3.0, "start", "line start"),
            SegmentMarker(1, 7.0, 10.0, "end", "line end"),
        ]
    )

    with pytest.raises(LLMError, match="uncovered transcript segment"):
        _validate_segments_cover_transcript_text(segments, transcript)


def test_cached_segments_valid_rejects_uncovered_transcript_text(tmp_path) -> None:  # type: ignore[no-untyped-def]
    transcript = Transcript(
        segments=[
            TranscriptSegment(
                id=0,
                start=0.0,
                end=10.0,
                text="abc",
                words=[
                    WordTimestamp("a", 0.0, 3.0, 1.0),
                    WordTimestamp("b", 4.0, 6.0, 1.0),
                    WordTimestamp("c", 7.0, 10.0, 1.0),
                ],
            )
        ],
        language="en",
        duration=10.0,
    )
    segments = SegmentList(
        markers=[
            SegmentMarker(0, 0.0, 3.0, "first", "first word"),
            SegmentMarker(1, 7.0, 10.0, "third", "third word"),
        ]
    )
    path = tmp_path / "segments.json"
    atomic_write_json(path, segments)

    assert not _cached_segments_valid(path, transcript)


def test_run_rejects_invalid_cached_segments_before_returning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transcript = Transcript(
        segments=[
            TranscriptSegment(
                id=0,
                start=0.0,
                end=10.0,
                text="abc",
                words=[
                    WordTimestamp("a", 0.0, 3.0, 1.0),
                    WordTimestamp("b", 4.0, 6.0, 1.0),
                    WordTimestamp("c", 7.0, 10.0, 1.0),
                ],
            )
        ],
        language="en",
        duration=10.0,
    )
    cached_segments = SegmentList(
        markers=[
            SegmentMarker(0, 0.0, 3.0, "first", "cached first word"),
            SegmentMarker(1, 7.0, 10.0, "third", "cached third word"),
        ]
    )
    regenerated_segments = SegmentList(
        markers=[SegmentMarker(0, 0.0, 10.0, "all", "regenerated coverage")]
    )
    source = tmp_path / "input.wav"
    source.write_bytes(b"audio")
    paths = build_paths(source, tmp_path / "cache", tmp_path / "output", "inputhash")
    paths.run_dir.mkdir(parents=True)
    atomic_write_json(paths.segments_json, cached_segments)
    audio = type("Audio", (), {"get_transcript": lambda self: transcript})()
    ctx = PipelineContext(
        source,
        "inputhash",
        "audio_only",
        _config(),
        paths,
        ArtifactBundle(audio=audio),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        segment_stage,
        "cached_output",
        lambda *args, **kwargs: StageOutput("segment", [paths.segments_json], True, "cached", {}),
    )
    monkeypatch.setattr(segment_stage, "for_task", lambda *args, **kwargs: object())
    called = {"complete_json": False}

    def complete_json(*args: object, **kwargs: object) -> SegmentList:
        called["complete_json"] = True
        return regenerated_segments

    monkeypatch.setattr(segment_stage, "complete_json", complete_json)

    output = segment_stage.run(ctx)

    assert called["complete_json"] is True
    assert output.cache_hit is False
    assert read_json_file(paths.segments_json, SegmentList) == regenerated_segments


def test_validate_segments_rejects_overlapping_boundaries() -> None:
    segments = SegmentList(
        markers=[
            SegmentMarker(0, 0.0, 6.0, "first", "overlaps next"),
            SegmentMarker(1, 5.0, 10.0, "second", "overlaps previous"),
        ]
    )

    with pytest.raises(LLMError, match="overlapping markers"):
        _validate_segments(segments, 10.0)
