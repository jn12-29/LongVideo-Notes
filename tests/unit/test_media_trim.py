from pathlib import Path
from types import SimpleNamespace

import pytest

from lvnotes.core.exceptions import MediaError
from lvnotes.media import trim


def test_make_head_trim_path_normalizes_minutes() -> None:
    assert trim.make_head_trim_path(Path("/tmp/lecture.mp4"), 10.0) == Path("/tmp/lecture.head-10m.mp4")
    assert trim.make_head_trim_path(Path("/tmp/lecture.mp3"), 2.5) == Path("/tmp/lecture.head-2.5m.mp3")


def test_trim_media_head_reuses_existing_output(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "lecture.mp4"
    output = tmp_path / "lecture.head-10m.mp4"
    source.write_bytes(b"source")
    output.write_bytes(b"trimmed")
    commands: list[list[str]] = []

    monkeypatch.setattr(trim, "probe_media", lambda path: SimpleNamespace(audio=object()))
    monkeypatch.setattr(trim, "_run_command", lambda args, tool_name: commands.append(args))

    assert trim.trim_media_head(source, 10.0) == output
    assert commands == []


def test_trim_media_head_creates_output_atomically(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"source")
    command_args: list[str] = []

    def run_command(args: list[str], tool_name: str) -> None:
        nonlocal command_args
        command_args = args
        Path(args[-1]).write_bytes(b"trimmed")

    monkeypatch.setattr(trim, "probe_media", lambda path: SimpleNamespace(audio=object()))
    monkeypatch.setattr(trim, "_run_command", run_command)

    output = trim.trim_media_head(source, 2.5)

    assert output == tmp_path / "lecture.head-2.5m.mp4"
    assert output.read_bytes() == b"trimmed"
    assert command_args[:4] == ["ffmpeg", "-y", "-i", str(source)]
    assert command_args[4:10] == ["-t", "150", "-map", "0", "-c", "copy"]


def test_resolve_head_trim_path_requires_existing_trim(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(trim, "probe_media", lambda path: SimpleNamespace(audio=object()))

    with pytest.raises(MediaError, match="trimmed media not found"):
        trim.resolve_head_trim_path(source, 10.0)


def test_trim_media_head_rejects_non_positive_minutes(tmp_path: Path) -> None:
    with pytest.raises(MediaError, match="greater than 0"):
        trim.trim_media_head(tmp_path / "lecture.mp4", 0)
