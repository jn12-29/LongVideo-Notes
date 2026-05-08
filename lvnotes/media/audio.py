from pathlib import Path
import os
import tempfile

from lvnotes.core.exceptions import MediaError
from lvnotes.media.probe import _ensure_output_file, _run_command, probe_media


def extract_wav(input_path: Path, output_path: Path, sample_rate: int, channels: int) -> Path:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{output_path.stem}.", suffix=output_path.suffix, dir=output_path.parent)
    os.close(fd)
    tmp_path = Path(tmp_name)
    tmp_path.unlink()
    _run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            str(tmp_path),
        ],
        "ffmpeg",
    )
    _ensure_output_file(tmp_path)
    if probe_media(tmp_path).audio is None:
        raise MediaError(f"extracted wav has no audio stream: {tmp_path}")
    os.replace(tmp_path, output_path)
    return output_path
