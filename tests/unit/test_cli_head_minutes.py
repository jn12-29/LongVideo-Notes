from pathlib import Path
from types import SimpleNamespace

import click

from lvnotes.cli import app
from lvnotes.core.config import AppConfig
from lvnotes.core.exceptions import MediaError


def test_make_context_uses_trimmed_path_for_hash_and_paths(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "lecture.mp4"
    trimmed = tmp_path / "lecture.head-10m.mp4"
    source.write_bytes(b"source")
    trimmed.write_bytes(b"trimmed")
    hashed_paths: list[Path] = []
    build_paths_sources: list[Path] = []

    monkeypatch.setattr(app, "configure_logging", lambda debug: None)
    monkeypatch.setattr(app, "load_config", lambda config_path: _config(tmp_path))
    monkeypatch.setattr(app, "trim_media_head", lambda path, minutes: trimmed)
    monkeypatch.setattr(app, "probe_media", lambda path: SimpleNamespace(audio=object(), video=object()))

    def hash_file(path: Path) -> str:
        hashed_paths.append(path)
        return "trimhash"

    def build_paths(source_path: Path, cache_dir: Path, output_dir: Path, input_hash: str):  # type: ignore[no-untyped-def]
        build_paths_sources.append(source_path)
        return _paths(tmp_path, source_path)

    monkeypatch.setattr(app, "hash_file", hash_file)
    monkeypatch.setattr(app, "build_paths", build_paths)

    ctx = app._make_context(source, None, False, False, False, False, head_minutes=10.0)

    assert ctx.source_path == trimmed
    assert hashed_paths == [trimmed]
    assert build_paths_sources == [trimmed]


def test_make_context_inspect_resolves_existing_trim_without_creating(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "lecture.mp4"
    trimmed = tmp_path / "lecture.head-10m.mp4"
    source.write_bytes(b"source")
    trimmed.write_bytes(b"trimmed")
    created: list[Path] = []
    resolved: list[Path] = []

    monkeypatch.setattr(app, "configure_logging", lambda debug: None)
    monkeypatch.setattr(app, "load_config", lambda config_path: _config(tmp_path))
    monkeypatch.setattr(app, "trim_media_head", lambda path, minutes: created.append(path) or trimmed)
    monkeypatch.setattr(app, "resolve_head_trim_path", lambda path, minutes: resolved.append(path) or trimmed)
    monkeypatch.setattr(app, "probe_media", lambda path: SimpleNamespace(audio=object(), video=None))
    monkeypatch.setattr(app, "hash_file", lambda path: "trimhash")
    monkeypatch.setattr(app, "build_paths", lambda source_path, cache_dir, output_dir, input_hash: _paths(tmp_path, source_path))

    ctx = app._make_context(source, None, False, False, False, False, head_minutes=10.0, create_trim=False)

    assert ctx.source_path == trimmed
    assert created == []
    assert resolved == [source.resolve()]


def test_make_context_inspect_missing_trim_reports_click_error(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"source")
    created: list[Path] = []

    monkeypatch.setattr(app, "configure_logging", lambda debug: None)
    monkeypatch.setattr(app, "trim_media_head", lambda path, minutes: created.append(path) or path)
    monkeypatch.setattr(app, "resolve_head_trim_path", lambda path, minutes: (_ for _ in ()).throw(MediaError("trimmed media not found: lecture.head-10m.mp4")))

    try:
        app._make_context(source, None, False, False, False, False, head_minutes=10.0, create_trim=False)
    except click.ClickException as exc:
        assert "trimmed media not found" in exc.message
    else:
        raise AssertionError("expected ClickException")
    assert created == []


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "project": {"cache_dir": tmp_path / "cache", "output_dir": tmp_path / "output"},
            "llm": {
                "profiles": {
                    "main": {"provider": "openai_compatible_chat", "base_url": "http://localhost:8000/v1", "api_key_env": None, "model": "test"},
                    "vlm": {"provider": "openai_compatible_chat", "base_url": "http://localhost:8000/v1", "api_key_env": None, "model": "test", "capabilities": ["vision"]},
                }
            },
            "tasks": {"segment": "main", "refine": "main", "outline": "main", "section": "main", "slide_judge": "vlm", "slide_describe": "main"},
        }
    )


def _paths(tmp_path: Path, source_path: Path) -> SimpleNamespace:
    run_dir = tmp_path / "cache" / "trimhash"
    return SimpleNamespace(
        source_path=source_path,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "output",
        run_dir=run_dir,
        audio_dir=run_dir / "audio",
        visual_dir=run_dir / "visual",
        visual_frames_dir=run_dir / "visual" / "frames",
        debug_dir=run_dir / "debug",
        refined_dir=run_dir / "refined",
        sections_dir=run_dir / "sections",
        output_note_md=tmp_path / "output" / "lecture-head-10m.md",
    )
