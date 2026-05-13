from pathlib import Path

from click.testing import CliRunner

from lvnotes.cli import app
from lvnotes.cli.output_tidy import build_output_tidy_plan


def test_build_output_tidy_plan_moves_timestamped_archives_under_archive_root(tmp_path: Path) -> None:
    output = tmp_path / "output"
    nested = output / "week1"
    nested.mkdir(parents=True)
    latest = nested / "lecture.md"
    archive = nested / "lecture-20260513-123456.md"
    assets = nested / "lecture-20260513-123456_assets"
    latest.write_text("# Latest", encoding="utf-8")
    archive.write_text("# Archive", encoding="utf-8")
    assets.mkdir()

    plan = build_output_tidy_plan(output)

    assert len(plan.moves) == 1
    move = plan.moves[0]
    assert move.source_md == archive
    assert move.destination_md == output / "_archive" / "week1" / archive.name
    assert move.source_assets == assets
    assert move.destination_assets == output / "_archive" / "week1" / assets.name
    assert plan.conflicts == []


def test_output_tidy_dry_run_does_not_create_or_move(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    output = tmp_path / "output"
    output.mkdir()
    latest = output / "lecture.md"
    archive = output / "lecture-20260513-123456.md"
    latest.write_text("# Latest", encoding="utf-8")
    archive.write_text("# Archive", encoding="utf-8")
    monkeypatch.setattr(app, "preload_cuda_libs", lambda: None)

    result = CliRunner().invoke(app.main, ["output", "tidy", str(output)])

    assert result.exit_code == 0
    assert "Mode: dry-run" in result.output
    assert archive.exists()
    assert not (output / "_archive").exists()


def test_output_tidy_apply_moves_archive_and_assets(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    output = tmp_path / "output"
    output.mkdir()
    latest = output / "lecture.md"
    archive = output / "lecture-20260513-123456.md"
    assets = output / "lecture-20260513-123456_assets"
    latest_assets = output / "lecture_assets"
    latest.write_text("# Latest", encoding="utf-8")
    archive.write_text("# Archive", encoding="utf-8")
    assets.mkdir()
    (assets / "000001.png").write_bytes(b"image")
    latest_assets.mkdir()
    monkeypatch.setattr(app, "preload_cuda_libs", lambda: None)

    result = CliRunner().invoke(app.main, ["output", "tidy", str(output), "--apply"])

    destination = output / "_archive" / "lecture-20260513-123456.md"
    destination_assets = output / "_archive" / "lecture-20260513-123456_assets"
    assert result.exit_code == 0
    assert "Moved archives: 1" in result.output
    assert latest.exists()
    assert latest_assets.exists()
    assert not archive.exists()
    assert destination.read_text(encoding="utf-8") == "# Archive"
    assert not assets.exists()
    assert (destination_assets / "000001.png").exists()


def test_output_tidy_apply_moves_nested_archive_under_archive_root(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    output = tmp_path / "output"
    nested = output / "week1"
    nested.mkdir(parents=True)
    latest = nested / "lecture.md"
    archive = nested / "lecture-20260513-123456.md"
    latest.write_text("# Latest", encoding="utf-8")
    archive.write_text("# Archive", encoding="utf-8")
    monkeypatch.setattr(app, "preload_cuda_libs", lambda: None)

    result = CliRunner().invoke(app.main, ["output", "tidy", str(output), "--apply"])

    assert result.exit_code == 0
    assert not archive.exists()
    assert (output / "_archive" / "week1" / archive.name).exists()


def test_output_tidy_apply_is_idempotent(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    output = tmp_path / "output"
    archive_root = output / "_archive"
    archive_root.mkdir(parents=True)
    (output / "lecture.md").write_text("# Latest", encoding="utf-8")
    (archive_root / "lecture-20260513-123456.md").write_text("# Archive", encoding="utf-8")
    monkeypatch.setattr(app, "preload_cuda_libs", lambda: None)

    result = CliRunner().invoke(app.main, ["output", "tidy", str(output), "--apply"])

    assert result.exit_code == 0
    assert "Moves: 0" in result.output
    assert "Moved archives: 0" in result.output


def test_output_tidy_only_skips_archive_root_not_nested_source_name(tmp_path: Path) -> None:
    output = tmp_path / "output"
    nested = output / "course" / "_archive"
    nested.mkdir(parents=True)
    latest = nested / "lecture.md"
    archive = nested / "lecture-20260513-123456.md"
    latest.write_text("# Latest", encoding="utf-8")
    archive.write_text("# Archive", encoding="utf-8")

    plan = build_output_tidy_plan(output)

    assert len(plan.moves) == 1
    assert plan.moves[0].destination_md == output / "_archive" / "course" / "_archive" / archive.name


def test_output_tidy_requires_latest_note_to_avoid_false_positive(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "meeting-20260513-123456.md").write_text("# Not lvnotes archive", encoding="utf-8")

    plan = build_output_tidy_plan(output)

    assert plan.moves == []
    assert plan.conflicts == []


def test_output_tidy_ignores_timestamped_md_directories(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "lecture.md").write_text("# Latest", encoding="utf-8")
    (output / "lecture-20260513-123456.md").mkdir()

    plan = build_output_tidy_plan(output)

    assert plan.moves == []
    assert plan.conflicts == []


def test_output_tidy_requires_latest_note_to_be_file(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "lecture.md").mkdir()
    (output / "lecture-20260513-123456.md").write_text("# Archive", encoding="utf-8")

    plan = build_output_tidy_plan(output)

    assert plan.moves == []
    assert plan.conflicts == []


def test_output_tidy_conflict_does_not_overwrite(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    output = tmp_path / "output"
    archive_root = output / "_archive"
    archive_root.mkdir(parents=True)
    latest = output / "lecture.md"
    archive = output / "lecture-20260513-123456.md"
    destination = archive_root / archive.name
    latest.write_text("# Latest", encoding="utf-8")
    archive.write_text("# New archive", encoding="utf-8")
    destination.write_text("# Existing archive", encoding="utf-8")
    monkeypatch.setattr(app, "preload_cuda_libs", lambda: None)

    result = CliRunner().invoke(app.main, ["output", "tidy", str(output), "--apply"])

    assert result.exit_code != 0
    assert "destination markdown already exists" in result.output
    assert archive.read_text(encoding="utf-8") == "# New archive"
    assert destination.read_text(encoding="utf-8") == "# Existing archive"


def test_output_tidy_conflicts_when_destination_assets_exist(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    output = tmp_path / "output"
    output.mkdir()
    (output / "lecture.md").write_text("# Latest", encoding="utf-8")
    (output / "lecture-20260513-123456.md").write_text("# Archive", encoding="utf-8")
    (output / "lecture-20260513-123456_assets").mkdir()
    (output / "_archive" / "lecture-20260513-123456_assets").mkdir(parents=True)
    monkeypatch.setattr(app, "preload_cuda_libs", lambda: None)

    result = CliRunner().invoke(app.main, ["output", "tidy", str(output), "--apply"])

    assert result.exit_code != 0
    assert "destination assets already exists" in result.output
    assert (output / "lecture-20260513-123456.md").exists()
    assert (output / "lecture-20260513-123456_assets").exists()


def test_output_tidy_conflicts_when_destination_parent_is_file(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    output = tmp_path / "output"
    nested = output / "week1"
    nested.mkdir(parents=True)
    (nested / "lecture.md").write_text("# Latest", encoding="utf-8")
    (nested / "lecture-20260513-123456.md").write_text("# Archive", encoding="utf-8")
    (output / "_archive").write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(app, "preload_cuda_libs", lambda: None)

    result = CliRunner().invoke(app.main, ["output", "tidy", str(output), "--apply"])

    assert result.exit_code != 0
    assert "destination parent is not a directory" in result.output
    assert (nested / "lecture-20260513-123456.md").exists()
