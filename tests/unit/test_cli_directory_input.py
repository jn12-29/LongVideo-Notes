from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from lvnotes.cli import app
from lvnotes.core.config import AppConfig
from lvnotes.core.paths import build_paths
from lvnotes.core.pipeline import StageOutput


def test_resolve_input_tasks_recurses_sorts_and_filters_media(tmp_path: Path) -> None:
    root = tmp_path / "courses"
    (root / "week2").mkdir(parents=True)
    (root / "week1").mkdir()
    (root / ".hidden").mkdir()
    (root / "week2" / "b.mp4").write_bytes(b"video")
    (root / "week1" / "a.mp3").write_bytes(b"audio")
    (root / "week1" / "a.head-10m.mp3").write_bytes(b"trim")
    (root / "week1" / "topic.head-final.mp3").write_bytes(b"audio")
    (root / "week1" / "notes.txt").write_text("skip", encoding="utf-8")
    (root / ".hidden" / "secret.mp4").write_bytes(b"skip")

    tasks = app._resolve_input_tasks(root)

    assert [task.source_path.name for task in tasks] == ["a.mp3", "topic.head-final.mp3", "b.mp4"]
    assert [task.output_subdir for task in tasks] == [Path("week1"), Path("week1"), Path("week2")]
    assert all(task.from_directory for task in tasks)


def test_run_directory_continues_after_failure_and_preserves_output_subdirs(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / "courses"
    (root / "week1").mkdir(parents=True)
    (root / "week2").mkdir()
    audio = root / "week1" / "a.mp3"
    video = root / "week2" / "b.mp4"
    audio.write_bytes(b"audio")
    video.write_bytes(b"video")
    seen: list[tuple[Path, str, Path]] = []

    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "_input_cache_lock", _noop_lock)
    monkeypatch.setattr(app, "_validate_multimodal_llm_profiles", lambda ctx: None)
    monkeypatch.setattr(app, "_run_multimodal_upstream", lambda ctx, debug: None)
    monkeypatch.setattr(app, "_run_audio_upstream", lambda ctx, debug: None)
    monkeypatch.setattr(app.describe, "run", lambda ctx: StageOutput("describe", [], False, "hash", {}))
    monkeypatch.setattr(app, "AudioArtifacts", lambda input_hash, paths: SimpleNamespace(is_complete=lambda: True))

    def run_stage_sequence(ctx, stages):  # type: ignore[no-untyped-def]
        seen.append((ctx.source_path, ctx.mode, ctx.paths.output_note_md))
        if ctx.source_path == audio.resolve():
            raise app.click.ClickException("boom")
        return SimpleNamespace(output_paths=[ctx.paths.output_note_md])

    monkeypatch.setattr(app, "_run_stage_sequence", run_stage_sequence)

    result = CliRunner().invoke(app.main, ["run", str(root), "--mm"])

    assert result.exit_code != 0
    assert "batch completed with 1 failure" in result.output
    assert seen == [
        (audio.resolve(), "audio_only", tmp_path / "output" / "week1" / "a.md"),
        (video.resolve(), "multimodal", tmp_path / "output" / "week2" / "b.md"),
    ]


def test_stage_directory_locks_each_input(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / "courses"
    root.mkdir()
    first = root / "a.mp3"
    second = root / "b.mp3"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    events: list[str] = []

    _patch_common(monkeypatch, tmp_path)

    @contextmanager
    def input_lock(ctx):  # type: ignore[no-untyped-def]
        events.append(f"lock:{ctx.source_path.name}")
        yield

    def stage_run(ctx):  # type: ignore[no-untyped-def]
        events.append(f"stage:{ctx.source_path.name}")
        return StageOutput("extract", [], False, "hash", {})

    new_command = app._stage_command("extract", stage_run, require_mm=False)
    monkeypatch.setattr(app, "_input_cache_lock", input_lock)
    monkeypatch.setattr(app.main.commands["extract"], "callback", new_command.callback)

    result = CliRunner().invoke(app.main, ["extract", str(root)])

    assert result.exit_code == 0
    assert events == ["lock:a.mp3", "stage:a.mp3", "lock:b.mp3", "stage:b.mp3"]


def test_single_audio_with_mm_runs_audio_only(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "lecture.mp3"
    source.write_bytes(b"audio")
    seen: list[str] = []

    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "_input_cache_lock", _noop_lock)
    monkeypatch.setattr(app, "_run_audio_upstream", lambda ctx, debug: seen.append(ctx.mode))
    monkeypatch.setattr(app, "_run_stage_sequence", lambda ctx, stages: SimpleNamespace(output_paths=[ctx.paths.output_note_md]))

    result = CliRunner().invoke(app.main, ["run", str(source), "--mm"])

    assert result.exit_code == 0
    assert seen == ["audio_only"]


def test_inspect_directory_paths_and_json_are_aggregated(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / "courses"
    (root / "week1").mkdir(parents=True)
    source = root / "week1" / "a.mp3"
    source.write_bytes(b"audio")

    _patch_common(monkeypatch, tmp_path)
    paths = build_paths(source.resolve(), tmp_path / "cache", tmp_path / "output", "inputhash", output_subdir=Path("week1"))
    paths.refined_transcript_json.parent.mkdir(parents=True)
    paths.refined_transcript_json.write_text('{"ok": true}', encoding="utf-8")

    paths_result = CliRunner().invoke(app.main, ["inspect", "audio", "refined", str(root), "--paths"])
    json_result = CliRunner().invoke(app.main, ["inspect", "audio", "refined", str(root), "--json"])

    assert paths_result.exit_code == 0
    assert f"{source.resolve()}\t{paths.refined_transcript_json}" in paths_result.output
    assert json_result.exit_code == 0
    payload = json.loads(json_result.output)
    assert payload["items"] == [{"source": str(source.resolve()), "path": str(paths.refined_transcript_json), "content": '{"ok": true}'}]
    assert payload["failures"] == []


def test_inspect_directory_json_keeps_raw_text_and_reports_failures_without_click_error(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / "courses"
    root.mkdir()
    good = root / "good.mp3"
    missing = root / "missing.mp3"
    good.write_bytes(b"audio")
    missing.write_bytes(b"audio")

    _patch_common(monkeypatch, tmp_path)
    good_paths = build_paths(good.resolve(), tmp_path / "cache", tmp_path / "output", "inputhash")
    missing_paths = build_paths(missing.resolve(), tmp_path / "cache", tmp_path / "output", "inputhash")
    good_paths.output_note_md.parent.mkdir(parents=True)
    good_paths.output_note_md.write_text("# Note", encoding="utf-8")

    result = CliRunner().invoke(app.main, ["inspect", "merge", "note", str(root), "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["items"] == [{"source": str(good.resolve()), "path": str(good_paths.output_note_md), "content": "# Note"}]
    assert payload["failures"] == [{"source": str(missing.resolve()), "message": f"artifact not found: {missing_paths.output_note_md}"}]


def _patch_common(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(app, "configure_logging", lambda debug: None)
    monkeypatch.setattr(app, "load_config", lambda config_path: _config(tmp_path))
    monkeypatch.setattr(app, "hash_file", lambda path: "inputhash")
    monkeypatch.setattr(app, "progress_write", lambda message: None)
    monkeypatch.setattr(app, "probe_media", lambda path: SimpleNamespace(audio=object(), video=object() if path.suffix == ".mp4" else None))


@contextmanager
def _noop_lock(ctx):  # type: ignore[no-untyped-def]
    yield


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
