from lvnotes.audio_pipeline.segment import _transcript_lines
from lvnotes.core.schemas import Transcript, TranscriptSegment, WordTimestamp


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
