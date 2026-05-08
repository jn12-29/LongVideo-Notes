from lvnotes.asr.base import Transcriber
from lvnotes.asr.faster_whisper_local import FasterWhisperLocalTranscriber
from lvnotes.core.config import ASRConfig
from lvnotes.core.exceptions import ASRError


def create_transcriber(config: ASRConfig) -> Transcriber:
    if config.backend == "faster_whisper_local":
        return FasterWhisperLocalTranscriber()
    raise ASRError(f"unsupported ASR backend: {config.backend}")
