import logging

import pytest

from lvnotes.audio_pipeline.segment import (
    _snap_segments_to_transcript_timestamps,
    _transcript_lines,
    _validate_segments,
)
from lvnotes.core.exceptions import LLMError
from lvnotes.core.schemas import SegmentList, SegmentMarker, Transcript, TranscriptSegment, WordTimestamp


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


def test_snap_segments_fails_when_boundary_is_too_far_from_timestamp() -> None:
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

    with pytest.raises(LLMError, match="nearest transcript timestamp"):
        _snap_segments_to_transcript_timestamps(segments, transcript)


def test_snap_segments_allows_boundary_at_failure_threshold() -> None:
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


def test_validate_segments_rejects_overlapping_boundaries() -> None:
    segments = SegmentList(
        markers=[
            SegmentMarker(0, 0.0, 6.0, "first", "overlaps next"),
            SegmentMarker(1, 5.0, 10.0, "second", "overlaps previous"),
        ]
    )

    with pytest.raises(LLMError, match="overlapping markers"):
        _validate_segments(segments, 10.0)
