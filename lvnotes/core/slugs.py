import re
import unicodedata

_UNSAFE_RE = re.compile(r"[\s\[\]()`*_{}<>#!|\\/?:;,.]+")
_DASH_RE = re.compile(r"-+")


def make_chapter_anchor(chapter_id: int, title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).strip().lower()
    safe = _UNSAFE_RE.sub("-", normalized)
    safe = _DASH_RE.sub("-", safe).strip("-")
    if safe:
        return f"chapter-{chapter_id}-{safe}"
    return f"chapter-{chapter_id}"
