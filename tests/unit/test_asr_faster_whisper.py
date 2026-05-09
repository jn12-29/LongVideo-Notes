from dataclasses import dataclass

import pytest

from lvnotes.asr import faster_whisper_local
from lvnotes.asr.faster_whisper_local import _consume_segments_with_progress, _normalize_segments
from lvnotes.core.config import ASRConfig
from lvnotes.core.exceptions import ASRError


@dataclass(frozen=True)
class RawSegment:
    start: object
    end: object
    text: str = "text"
    words: object = None


def test_consume_segments_rejects_invalid_end_timestamp() -> None:
    with pytest.raises(TypeError):
        _consume_segments_with_progress([RawSegment(0.0, None)], duration=1.0)


def test_normalize_segments_rejects_unordered_segments() -> None:
    segments = [RawSegment(0.0, 2.0), RawSegment(1.0, 3.0)]

    with pytest.raises(AssertionError, match="ordered"):
        _normalize_segments(segments)


def test_transcribe_wraps_invalid_backend_segments(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    class Model:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def transcribe(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            return [RawSegment(0.0, 2.0), RawSegment(1.0, 3.0)], type("Info", (), {"duration": 3.0, "language": "zh"})()

    monkeypatch.setattr(faster_whisper_local, "_load_faster_whisper", lambda: (Model, None))

    with pytest.raises(ASRError, match="ASR segments must be ordered"):
        faster_whisper_local.FasterWhisperLocalTranscriber().transcribe(tmp_path / "audio.wav", ASRConfig())
