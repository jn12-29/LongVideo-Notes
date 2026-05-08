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
from lvnotes.core.schemas import ContentBlock, Outline


def read_blocks(path: Path) -> list[ContentBlock]:
    return read_json_file(path, list[ContentBlock])  # type: ignore[return-value]


def read_outline(path: Path) -> Outline:
    return read_json_file(path, Outline)  # type: ignore[return-value]


def cache_output(
    stage_name: str,
    output_paths: list[Path],
    cache_key: str,
    input_hashes: dict[str, str],
    config_hash: str,
    prompt_hash: str | None,
    metadata: dict[str, MetadataValue] | None = None,
    manifest_output_path: Path | None = None,
) -> StageOutput:
    manifest_target = manifest_output_path or output_paths[0]
    write_cache_manifest(
        cache_manifest_path(manifest_target),
        CacheManifest(stage_name, cache_key, input_hashes, config_hash, prompt_hash, output_paths),
    )
    content_hash = hash_file(output_paths[0]) if len(output_paths) == 1 else combined_content_hash(output_paths)
    return StageOutput(stage_name, output_paths, False, content_hash, {"cache_key": cache_key, **(metadata or {})})


def cached_output(stage_name: str, output_paths: list[Path], cache_key: str, manifest_output_path: Path | None = None) -> StageOutput | None:
    manifest_target = manifest_output_path or output_paths[0]
    if not is_cache_hit(cache_manifest_path(manifest_target), cache_key, output_paths):
        return None
    content_hash = hash_file(output_paths[0]) if len(output_paths) == 1 else combined_content_hash(output_paths)
    return StageOutput(stage_name, output_paths, True, content_hash, {"cache_key": cache_key})


def prompt_path(name: str) -> Path:
    return Path(__file__).with_name("prompts") / name
