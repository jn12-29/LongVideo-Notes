from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from lvnotes.core.serialization import JsonValue, from_jsonable, to_jsonable


@dataclass(frozen=True)
class CacheManifest:
    stage_name: str
    cache_key: str
    input_hashes: dict[str, str]
    config_hash: str
    prompt_hash: str | None
    output_paths: list[Path]


def atomic_write_json(path: Path, payload: object) -> None:
    text = json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    atomic_write_text(path, text)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_dir(path.parent)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_json(payload: object) -> str:
    content = json.dumps(to_jsonable(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def hash_prompt_template(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_cache_key(stage_name: str, parts: dict[str, str]) -> str:
    return hash_json({"stage_name": stage_name, "parts": parts})


def cache_manifest_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.name}.cache.json")


def read_cache_manifest(path: Path) -> CacheManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return from_jsonable(CacheManifest, payload)  # type: ignore[return-value]


def write_cache_manifest(path: Path, manifest: CacheManifest) -> None:
    atomic_write_json(path, manifest)


def is_cache_hit(manifest_path: Path, expected_cache_key: str, output_paths: list[Path]) -> bool:
    if not manifest_path.exists():
        return False
    manifest = read_cache_manifest(manifest_path)
    if manifest.cache_key != expected_cache_key:
        return False
    expected = [path.resolve() for path in output_paths]
    actual = [path.resolve() for path in manifest.output_paths]
    if expected != actual:
        return False
    return all(path.exists() for path in output_paths)


def combined_content_hash(paths: list[Path]) -> str:
    return hash_json([{"path": str(path), "hash": hash_file(path)} for path in paths])


def read_json_file(path: Path, schema: type[object]) -> object:
    payload: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    return from_jsonable(schema, payload)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
