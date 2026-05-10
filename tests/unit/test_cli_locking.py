from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from lvnotes.cli import app
from lvnotes.core.config import AppConfig
from lvnotes.core.locks import input_cache_lock_path
from lvnotes.core.pipeline import StageOutput


@pytest.mark.parametrize(
    ("stage_name", "requires_mm"),
    [(name, False) for name in app.AUDIO_STAGES | app.MERGE_STAGES] + [(name, True) for name in app.VISUAL_STAGES],
)
def test_stage_command_runs_inside_input_lock(monkeypatch, tmp_path: Path, stage_name: str, requires_mm: bool) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / ("lecture.mp4" if requires_mm else "lecture.mp3")
    source.write_bytes(b"source")
    events: list[str] = []
    _patch_context(monkeypatch, tmp_path, video=requires_mm)

    @contextmanager
    def input_lock(ctx):  # type: ignore[no-untyped-def]
        events.append("lock-enter")
        assert not ctx.paths.audio_dir.exists()
        app._ensure_runtime_dirs(ctx.paths)
        if stage_name == "describe":
            ctx.paths.refined_transcript_json.write_text("{}", encoding="utf-8")
        yield
        events.append("lock-exit")

    def stage_run(ctx):  # type: ignore[no-untyped-def]
        events.append("stage")
        assert ctx.paths.audio_dir.exists()
        return StageOutput("extract", [], False, "hash", {})

    new_command = app._stage_command(stage_name, stage_run, require_mm=requires_mm)
    monkeypatch.setattr(app, "_input_cache_lock", input_lock)
    monkeypatch.setattr(app.main.commands[stage_name], "callback", new_command.callback)

    args = [stage_name, str(source)] + (["--mm"] if requires_mm else [])
    result = CliRunner().invoke(app.main, args)

    assert result.exit_code == 0
    assert events == ["lock-enter", "stage", "lock-exit"]


def test_input_cache_lock_creates_runtime_dirs_inside_real_lock(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "lecture.mp3"
    source.write_bytes(b"source")
    _patch_context(monkeypatch, tmp_path)
    ctx = app._make_context(source, None, False, False, False, False, create_dirs=False)

    with app._input_cache_lock(ctx):
        assert input_cache_lock_path(ctx.paths.run_dir).exists()
        assert ctx.paths.audio_dir.exists()
        with pytest.raises(Exception, match="already held"):
            from lvnotes.core.locks import input_cache_lock

            with input_cache_lock(ctx.paths.run_dir, blocking=False):
                pass


def test_inspect_does_not_use_input_lock(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "lecture.mp3"
    source.write_bytes(b"source")
    _patch_context(monkeypatch, tmp_path)

    def input_lock(ctx):  # type: ignore[no-untyped-def]
        raise AssertionError("inspect must not lock")

    monkeypatch.setattr(app, "_input_cache_lock", input_lock)

    result = CliRunner().invoke(app.main, ["inspect", "audio", "refined", str(source), "--paths"])

    assert result.exit_code == 0
    assert not (tmp_path / "cache").exists()


def test_inspect_head_minutes_does_not_create_trim_or_lock(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "lecture.mp3"
    trimmed = tmp_path / "lecture.head-10m.mp3"
    source.write_bytes(b"source")
    trimmed.write_bytes(b"trimmed")
    created: list[Path] = []
    _patch_context(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "resolve_head_trim_path", lambda path, minutes: trimmed)
    monkeypatch.setattr(app, "trim_media_head", lambda path, minutes: created.append(path) or trimmed)
    monkeypatch.setattr(app, "_input_cache_lock", lambda ctx: (_ for _ in ()).throw(AssertionError("inspect must not lock")))

    result = CliRunner().invoke(app.main, ["inspect", "audio", "refined", str(source), "--head-minutes", "10", "--paths"])

    assert result.exit_code == 0
    assert created == []
    assert not (tmp_path / ".lecture.head-10m.mp3.lock").exists()


def test_multimodal_run_validates_vlm_profiles_before_upstream(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"source")
    calls: list[str] = []
    _patch_context(monkeypatch, tmp_path, video=True)
    monkeypatch.setattr(app, "for_task", lambda config, task_name: calls.append(task_name) or object())
    monkeypatch.setattr(app, "_run_multimodal_upstream", lambda ctx, debug: (_ for _ in ()).throw(AssertionError("should validate first")))

    result = CliRunner().invoke(app.main, ["run", str(source), "--mm"])

    assert result.exit_code != 0
    assert calls == ["slide_judge", "slide_describe"]


def _patch_context(monkeypatch, tmp_path: Path, video: bool = False) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(app, "configure_logging", lambda debug: None)
    monkeypatch.setattr(app, "load_config", lambda config_path: _config(tmp_path))
    monkeypatch.setattr(app, "probe_media", lambda path: SimpleNamespace(audio=object(), video=object() if video else None))
    monkeypatch.setattr(app, "hash_file", lambda path: "inputhash")
    monkeypatch.setattr(app, "progress_write", lambda message: None)


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
