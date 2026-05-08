from pathlib import Path

from lvnotes.core.cache import (
    CacheManifest,
    cache_manifest_path,
    combined_content_hash,
    hash_file,
    is_cache_hit,
    read_json_file,
    write_cache_manifest,
)
from lvnotes.core.pipeline import MetadataValue, StageOutput
from lvnotes.core.schemas import VisualJudgementList, VisualSampleIndex, VisualSegmentList, VisualSelection


def read_samples(path: Path) -> VisualSampleIndex:
    return read_json_file(path, VisualSampleIndex)  # type: ignore[return-value]


def read_segments(path: Path) -> VisualSegmentList:
    return read_json_file(path, VisualSegmentList)  # type: ignore[return-value]


def read_judgements(path: Path) -> VisualJudgementList:
    return read_json_file(path, VisualJudgementList)  # type: ignore[return-value]


def read_selections(path: Path) -> list[VisualSelection]:
    return read_json_file(path, list[VisualSelection])  # type: ignore[return-value]


def cache_output(
    stage_name: str,
    output_paths: list[Path],
    cache_key: str,
    input_hashes: dict[str, str],
    config_hash: str,
    prompt_hash: str | None,
    metadata: dict[str, MetadataValue] | None = None,
) -> StageOutput:
    write_cache_manifest(cache_manifest_path(output_paths[0]), CacheManifest(stage_name, cache_key, input_hashes, config_hash, prompt_hash, output_paths))
    content_hash = hash_file(output_paths[0]) if len(output_paths) == 1 else combined_content_hash(output_paths)
    return StageOutput(stage_name, output_paths, False, content_hash, {"cache_key": cache_key, **(metadata or {})})


def cached_output(stage_name: str, output_paths: list[Path], cache_key: str) -> StageOutput | None:
    if not is_cache_hit(cache_manifest_path(output_paths[0]), cache_key, output_paths):
        return None
    content_hash = hash_file(output_paths[0]) if len(output_paths) == 1 else combined_content_hash(output_paths)
    return StageOutput(stage_name, output_paths, True, content_hash, {"cache_key": cache_key})


def prompt_path(name: str) -> Path:
    return Path(__file__).with_name("prompts") / name
