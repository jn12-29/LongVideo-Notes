from pathlib import Path
import os
import tempfile

from lvnotes.core.exceptions import MediaError
from lvnotes.media.probe import _ensure_output_file, _run_command, probe_media


def trim_media_head(input_path: Path, head_minutes: float, reuse: bool = True) -> Path:
    if head_minutes <= 0:
        raise MediaError("--head-minutes must be greater than 0")
    output_path = make_head_trim_path(input_path, head_minutes)
    if reuse and output_path.exists():
        _validate_trimmed_media(output_path)
        return output_path

    fd, tmp_name = tempfile.mkstemp(prefix=f".{output_path.stem}.", suffix=output_path.suffix, dir=output_path.parent)
    os.close(fd)
    tmp_path = Path(tmp_name)
    tmp_path.unlink()
    seconds = _format_seconds(head_minutes * 60)
    try:
        _run_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-t",
                seconds,
                "-map",
                "0",
                "-c",
                "copy",
                str(tmp_path),
            ],
            "ffmpeg",
        )
        _validate_trimmed_media(tmp_path)
        os.replace(tmp_path, output_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return output_path


def resolve_head_trim_path(input_path: Path, head_minutes: float) -> Path:
    if head_minutes <= 0:
        raise MediaError("--head-minutes must be greater than 0")
    output_path = make_head_trim_path(input_path, head_minutes)
    if not output_path.exists():
        raise MediaError(f"trimmed media not found: {output_path}")
    _validate_trimmed_media(output_path)
    return output_path


def make_head_trim_path(input_path: Path, head_minutes: float) -> Path:
    label = _format_minutes(head_minutes)
    return input_path.with_name(f"{input_path.stem}.head-{label}m{input_path.suffix}")


def _validate_trimmed_media(path: Path) -> None:
    _ensure_output_file(path)
    if probe_media(path).audio is None:
        raise MediaError(f"trimmed media has no audio stream: {path}")


def _format_minutes(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _format_seconds(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")
