from pathlib import Path

from lvnotes.core.cache import (
    CacheManifest,
    cache_manifest_path,
    combined_content_hash,
    hash_file,
    is_cache_hit,
    write_cache_manifest,
)
from lvnotes.core.pipeline import MetadataValue, StageOutput


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
    manifest = CacheManifest(
        stage_name=stage_name,
        cache_key=cache_key,
        input_hashes=input_hashes,
        config_hash=config_hash,
        prompt_hash=prompt_hash,
        output_paths=output_paths,
    )
    write_cache_manifest(cache_manifest_path(manifest_target), manifest)
    output_hash = hash_file(output_paths[0]) if len(output_paths) == 1 else combined_content_hash(output_paths)
    return StageOutput(
        stage_name=stage_name,
        output_paths=output_paths,
        cache_hit=False,
        content_hash=output_hash,
        metadata={"cache_key": cache_key, **(metadata or {})},
    )


def cached_output(stage_name: str, output_paths: list[Path], cache_key: str, manifest_output_path: Path | None = None) -> StageOutput | None:
    manifest_target = manifest_output_path or output_paths[0]
    if not is_cache_hit(cache_manifest_path(manifest_target), cache_key, output_paths):
        return None
    output_hash = hash_file(output_paths[0]) if len(output_paths) == 1 else combined_content_hash(output_paths)
    return StageOutput(
        stage_name=stage_name,
        output_paths=output_paths,
        cache_hit=True,
        content_hash=output_hash,
        metadata={"cache_key": cache_key},
    )


def prompt_path(name: str) -> Path:
    return Path(__file__).with_name("prompts") / name
