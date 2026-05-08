import logging
from pathlib import Path

from lvnotes.core.config import ASRConfig
from lvnotes.core.exceptions import ASRError
from lvnotes.core.schemas import Transcript, TranscriptSegment, WordTimestamp
from lvnotes.core.timestamps import normalize_seconds

log = logging.getLogger(__name__)


class FasterWhisperLocalTranscriber:
    def transcribe(self, audio_path: Path, config: ASRConfig) -> Transcript:
        whisper_model_class, batched_pipeline_class = _load_faster_whisper()
        device = _resolve_device(config.device)
        compute_type = _resolve_compute_type(config.compute_type, device)
        log.info("loading faster-whisper model %s", config.model)
        try:
            model = whisper_model_class(config.model, device=device, compute_type=compute_type)
        except RuntimeError as exc:
            raise ASRError(f"faster-whisper model loading failed: {exc}") from exc
        log.debug("faster-whisper device=%s compute_type=%s", device, compute_type)

        transcriber, batch_size = _transcriber(model, batched_pipeline_class, config, device)
        try:
            segments, transcript_info = _run_transcribe(transcriber, audio_path, config, batch_size)
        except RuntimeError as exc:
            raise ASRError(f"faster-whisper inference failed: {exc}") from exc
        normalized_segments = _normalize_segments(list(segments))
        if not normalized_segments:
            raise ASRError("no speech detected")
        return Transcript(
            segments=normalized_segments,
            language=_language(transcript_info, config.language),
            duration=_duration(transcript_info),
        )


def _load_faster_whisper() -> tuple[type[object], type[object] | None]:
    try:
        from faster_whisper import BatchedInferencePipeline, WhisperModel

        return WhisperModel, BatchedInferencePipeline
    except ImportError as exc:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ASRError("faster-whisper package is required for ASR") from exc
        return WhisperModel, None


def _resolve_device(configured_device: str) -> str:
    if configured_device != "auto":
        return configured_device
    try:
        import torch
    except ImportError:
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def _resolve_compute_type(configured_compute_type: str, device: str) -> str:
    if configured_compute_type != "auto":
        return configured_compute_type
    return "float16" if device == "cuda" else "int8"


def _transcriber(
    model: object,
    batched_pipeline_class: type[object] | None,
    config: ASRConfig,
    device: str,
) -> tuple[object, int | None]:
    if config.use_batched and device == "cuda" and batched_pipeline_class is not None:
        log.debug("using faster-whisper batched pipeline with batch_size=%s", config.batch_size)
        return batched_pipeline_class(model=model), config.batch_size
    log.debug("using faster-whisper non-batched pipeline")
    return model, None


def _run_transcribe(
    transcriber: object,
    audio_path: Path,
    config: ASRConfig,
    batch_size: int | None,
) -> tuple[object, object]:
    kwargs: dict[str, object] = {
        "language": config.language,
        "word_timestamps": True,
        "vad_filter": config.vad,
        "condition_on_previous_text": False,
        "initial_prompt": _initial_prompt_for_language(config.language),
    }
    if batch_size is not None:
        kwargs["batch_size"] = batch_size
    return transcriber.transcribe(str(audio_path), **kwargs)


def _normalize_segments(segments: list[object]) -> list[TranscriptSegment]:
    normalized: list[TranscriptSegment] = []
    for segment_id, segment in enumerate(segments):
        start = normalize_seconds(float(getattr(segment, "start")))
        end = normalize_seconds(float(getattr(segment, "end")))
        if start >= end:
            raise AssertionError("ASR segment start must be less than end")
        text = str(getattr(segment, "text", "")).strip()
        if text == "":
            continue
        normalized.append(
            TranscriptSegment(
                id=len(normalized),
                start=start,
                end=end,
                text=text,
                words=_normalize_words(getattr(segment, "words", None)),
            )
        )
    return normalized


def _normalize_words(words: object) -> list[WordTimestamp]:
    if words is None:
        return []
    normalized: list[WordTimestamp] = []
    for word in words:
        start = normalize_seconds(float(getattr(word, "start")))
        end = normalize_seconds(float(getattr(word, "end")))
        if start >= end:
            raise AssertionError("ASR word start must be less than end")
        normalized.append(
            WordTimestamp(
                word=str(getattr(word, "word", "")).strip(),
                start=start,
                end=end,
                probability=float(getattr(word, "probability", 0.0)),
            )
        )
    return sorted(normalized, key=lambda word: word.start)


def _language(transcript_info: object, fallback: str) -> str:
    language = getattr(transcript_info, "language", None)
    return language if isinstance(language, str) and language else fallback


def _duration(transcript_info: object) -> float:
    duration = getattr(transcript_info, "duration", None)
    return normalize_seconds(float(duration)) if duration is not None else 0.0


def _initial_prompt_for_language(language: str) -> str | None:
    if language == "zh":
        return "以下是中文课程讲解转录，请使用自然的中文标点。"
    return None
