from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import tempfile

from lvnotes.core.exceptions import MediaError
from lvnotes.core.timestamps import normalize_seconds
from lvnotes.media.probe import _run_command, probe_media


@dataclass(frozen=True)
class ExtractedFrame:
    path: Path
    timestamp: float


def extract_frames(input_path: Path, output_dir: Path, fps: float, filename_pattern: str) -> list[ExtractedFrame]:
    if fps <= 0:
        raise MediaError("fps must be greater than 0")
    pattern_path = Path(filename_pattern)
    if pattern_path.name != filename_pattern:
        raise MediaError(f"filename_pattern must be a single filename: {filename_pattern}")
    if probe_media(input_path).video is None:
        raise MediaError(f"input has no video stream: {input_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix=".frames.", dir=output_dir))
    try:
        _run_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-vf",
                f"fps={fps}",
                str(tmp_dir / filename_pattern),
            ],
            "ffmpeg",
        )
        tmp_paths = _matching_frame_paths(tmp_dir, filename_pattern)
        if not tmp_paths:
            raise MediaError(f"ffmpeg did not produce frames in {tmp_dir}")
        for tmp_path in tmp_paths:
            os.replace(tmp_path, output_dir / tmp_path.name)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    paths = _matching_frame_paths(output_dir, filename_pattern)
    if not paths:
        raise MediaError(f"ffmpeg did not produce frames in {output_dir}")
    return [ExtractedFrame(path=path, timestamp=normalize_seconds(index / fps)) for index, path in enumerate(paths)]


def _matching_frame_paths(output_dir: Path, filename_pattern: str) -> list[Path]:
    regex = _filename_regex(filename_pattern)
    matches = [path for path in output_dir.iterdir() if path.is_file() and regex.fullmatch(path.name)]
    return sorted(matches, key=lambda path: path.name)


def _filename_regex(filename_pattern: str) -> re.Pattern[str]:
    match = re.search(r"%0?\d*d", filename_pattern)
    if match is None:
        raise MediaError(f"filename_pattern must contain a numeric placeholder: {filename_pattern}")
    prefix = re.escape(filename_pattern[: match.start()])
    suffix = re.escape(filename_pattern[match.end() :])
    return re.compile(f"{prefix}\\d+{suffix}")
