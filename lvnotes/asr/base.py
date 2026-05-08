from pathlib import Path
from typing import Protocol

from lvnotes.core.config import ASRConfig
from lvnotes.core.schemas import Transcript


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path, config: ASRConfig) -> Transcript:
        """Transcribe an audio file into the normalized project schema."""
        ...
