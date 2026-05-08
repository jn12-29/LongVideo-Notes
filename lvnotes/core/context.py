from dataclasses import dataclass
from pathlib import Path

from lvnotes.core.artifacts import AudioArtifacts, VisualArtifacts
from lvnotes.core.config import AppConfig
from lvnotes.core.paths import PipelinePaths


@dataclass(frozen=True)
class ArtifactBundle:
    audio: AudioArtifacts
    visual: VisualArtifacts | None = None


@dataclass(frozen=True)
class PipelineContext:
    source_path: Path
    input_hash: str
    mode: str
    config: AppConfig
    paths: PipelinePaths
    artifacts: ArtifactBundle
    debug: bool = False
    no_cache: bool = False
