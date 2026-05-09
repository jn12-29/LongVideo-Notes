from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    path: Path
    mime_type: str


LLMContentPart = TextPart | ImagePart


@dataclass(frozen=True)
class LLMMessage:
    role: str
    content: list[LLMContentPart]


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cached_input_tokens: int | None = None


@dataclass(frozen=True)
class LLMTextResult:
    text: str
    model: str
    usage: LLMUsage | None
    raw_response_id: str | None = None


@dataclass(frozen=True)
class LLMRequestOptions:
    temperature: float | None = None
    max_output_tokens: int | None = None
    json_mode: bool = False
    timeout_seconds: float | None = None
    reasoning_effort: str | None = None
    thinking_budget_tokens: int | None = None
