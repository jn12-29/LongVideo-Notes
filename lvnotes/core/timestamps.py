import math
import re

_TS_MARKER_RE = re.compile(r"^\[\[TS:(\d+(?:\.\d+)?)\]\]$")


def normalize_seconds(seconds: float) -> float:
    return round(float(seconds), 3)


def format_hms(seconds: float) -> str:
    total = int(math.floor(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_mmss(seconds: float) -> str:
    total = int(math.floor(seconds))
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


def render_timestamp(seconds: float, template: str) -> str:
    normalized = normalize_seconds(seconds)
    return template.format(
        hms=format_hms(normalized),
        mmss=format_mmss(normalized),
        seconds=f"{normalized:.3f}",
        seconds_int=int(math.floor(normalized)),
    )


def parse_ts_marker(marker: str) -> float:
    match = _TS_MARKER_RE.match(marker)
    if match is None:
        raise ValueError(f"invalid timestamp marker: {marker}")
    return normalize_seconds(float(match.group(1)))
