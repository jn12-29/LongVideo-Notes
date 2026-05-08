from dataclasses import dataclass
import json
from pathlib import Path
import subprocess

from lvnotes.core.exceptions import MediaError
from lvnotes.core.timestamps import normalize_seconds


@dataclass(frozen=True)
class AudioStreamInfo:
    codec: str
    sample_rate: int
    channels: int
    duration: float | None


@dataclass(frozen=True)
class VideoStreamInfo:
    codec: str
    width: int
    height: int
    fps: float
    duration: float | None


@dataclass(frozen=True)
class MediaProbeResult:
    path: Path
    duration: float
    audio: AudioStreamInfo | None
    video: VideoStreamInfo | None


def probe_media(input_path: Path) -> MediaProbeResult:
    completed = _run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(input_path),
        ],
        "ffprobe",
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise MediaError(f"ffprobe returned invalid JSON for {input_path}: {exc}") from exc

    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise MediaError(f"ffprobe output missing streams for {input_path}")
    format_payload = payload.get("format", {})
    if not isinstance(format_payload, dict):
        raise MediaError(f"ffprobe output missing format for {input_path}")

    try:
        audio = _parse_audio_stream(streams)
        video = _parse_video_stream(streams)
        duration = _first_duration(format_payload.get("duration"), audio.duration if audio else None, video.duration if video else None)
    except (TypeError, ValueError) as exc:
        raise MediaError(f"failed to parse ffprobe output for {input_path}: {exc}") from exc
    return MediaProbeResult(path=input_path, duration=duration, audio=audio, video=video)


def has_audio_stream(input_path: Path) -> bool:
    return probe_media(input_path).audio is not None


def has_video_stream(input_path: Path) -> bool:
    return probe_media(input_path).video is not None


def _run_command(args: list[str], tool_name: str) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(args, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise MediaError(f"failed to execute {tool_name}: {exc}") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise MediaError(f"{tool_name} failed: {stderr}")
    return completed


def _ensure_output_file(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        raise MediaError(f"media output file is missing or empty: {path}")


def _parse_audio_stream(streams: list[object]) -> AudioStreamInfo | None:
    for stream in streams:
        if not isinstance(stream, dict) or stream.get("codec_type") != "audio":
            continue
        return AudioStreamInfo(
            codec=str(stream.get("codec_name") or "unknown"),
            sample_rate=int(str(stream.get("sample_rate") or "0")),
            channels=int(stream.get("channels") or 0),
            duration=_optional_duration(stream.get("duration")),
        )
    return None


def _parse_video_stream(streams: list[object]) -> VideoStreamInfo | None:
    for stream in streams:
        if not isinstance(stream, dict) or stream.get("codec_type") != "video":
            continue
        return VideoStreamInfo(
            codec=str(stream.get("codec_name") or "unknown"),
            width=int(stream.get("width") or 0),
            height=int(stream.get("height") or 0),
            fps=_parse_fraction(str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1")),
            duration=_optional_duration(stream.get("duration")),
        )
    return None


def _first_duration(*values: object) -> float:
    for value in values:
        duration = _optional_duration(value)
        if duration is not None:
            return duration
    raise MediaError("ffprobe output missing media duration")


def _optional_duration(value: object) -> float | None:
    if value is None or value == "N/A":
        return None
    try:
        return normalize_seconds(float(value))
    except (TypeError, ValueError):
        return None


def _parse_fraction(value: str) -> float:
    if "/" not in value:
        return float(value)
    numerator_text, denominator_text = value.split("/", 1)
    denominator = float(denominator_text)
    if denominator == 0:
        return 0.0
    return float(numerator_text) / denominator
