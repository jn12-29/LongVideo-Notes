from dataclasses import dataclass
from pathlib import Path


MetadataValue = str | int | float | bool | None


@dataclass(frozen=True)
class StageOutput:
    stage_name: str
    output_paths: list[Path]
    cache_hit: bool
    content_hash: str
    metadata: dict[str, MetadataValue]
