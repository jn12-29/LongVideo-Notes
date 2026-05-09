import base64

from lvnotes.core.config import LLMProfile
from lvnotes.core.exceptions import AuthError, LLMError, RateLimitError, TransportError
from lvnotes.llm.options import apply_profile_defaults, validate_reasoning_options
from lvnotes.llm.types import ImagePart, LLMMessage, LLMRequestOptions, LLMTextResult, LLMUsage, TextPart


class AnthropicMessagesClient:
    def __init__(self, profile: LLMProfile, api_key: str) -> None:
        self._profile = profile
        self._client = _create_anthropic_client(profile, api_key)

    @property
    def profile(self) -> LLMProfile:
        return self._profile

    def complete(self, messages: list[LLMMessage], options: LLMRequestOptions | None = None) -> LLMTextResult:
        request_options = apply_profile_defaults(self._profile, options or LLMRequestOptions())
        validate_reasoning_options(self._profile, request_options)
        system_text, request_messages = _messages(messages)
        max_tokens = request_options.max_output_tokens if request_options.max_output_tokens is not None else ((request_options.thinking_budget_tokens + 1024) if request_options.thinking_budget_tokens is not None else 1024)
        if request_options.thinking_budget_tokens is not None and request_options.thinking_budget_tokens >= max_tokens:
            raise LLMError("thinking_budget_tokens must be less than max_output_tokens for anthropic_messages")
        request_payload = {
            "model": self._profile.model,
            "max_tokens": max_tokens,
            "temperature": request_options.temperature,
            "system": system_text or None,
            "messages": request_messages,
            "timeout": request_options.timeout_seconds or self._profile.timeout_seconds,
        }
        if request_options.thinking_budget_tokens is not None:
            request_payload["thinking"] = {"type": "enabled", "budget_tokens": request_options.thinking_budget_tokens}
        try:
            response = self._client.messages.create(**request_payload)
        except Exception as exc:
            raise _normalize_anthropic_error(exc) from exc
        return LLMTextResult(
            text=_response_text(response.content),
            model=response.model,
            usage=_usage(response.usage),
            raw_response_id=response.id,
        )


def _create_anthropic_client(profile: LLMProfile, api_key: str) -> object:
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise LLMError("anthropic package is required for Anthropic providers") from exc
    try:
        return Anthropic(api_key=api_key, base_url=profile.base_url)
    except Exception as exc:
        raise _normalize_anthropic_error(exc) from exc


def _messages(messages: list[LLMMessage]) -> tuple[str, list[dict[str, object]]]:
    system_parts: list[str] = []
    request_messages: list[dict[str, object]] = []
    for message in messages:
        if message.role == "system":
            system_parts.extend(part.text for part in message.content if isinstance(part, TextPart))
        else:
            request_messages.append({"role": message.role, "content": _content(message.content)})
    return "\n".join(system_parts), request_messages


def _content(parts: list[TextPart | ImagePart]) -> list[dict[str, object]]:
    content: list[dict[str, object]] = []
    for part in parts:
        if isinstance(part, TextPart):
            content.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            media_type, encoded = _encoded_image(part)
            content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": encoded}})
    return content


def _encoded_image(part: ImagePart) -> tuple[str, str]:
    encoded = base64.b64encode(part.path.read_bytes()).decode("ascii")
    return part.mime_type, encoded


def _response_text(content: object) -> str:
    texts: list[str] = []
    if isinstance(content, list):
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                texts.append(text)
    return "\n".join(texts)


def _usage(usage: object) -> LLMUsage | None:
    if usage is None:
        return None
    input_tokens = _int_attr(usage, "input_tokens")
    output_tokens = _int_attr(usage, "output_tokens")
    total = input_tokens + output_tokens if input_tokens is not None and output_tokens is not None else None
    return LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total)


def _int_attr(source: object, name: str) -> int | None:
    value = getattr(source, name, None)
    return value if isinstance(value, int) else None


def _normalize_anthropic_error(exc: Exception) -> LLMError:
    status_code = getattr(exc, "status_code", None)
    if status_code in {401, 403}:
        return AuthError(f"Anthropic authentication failed: {type(exc).__name__}")
    if status_code == 429:
        return RateLimitError(f"Anthropic rate limit reached: {type(exc).__name__}")
    if isinstance(status_code, int) and status_code >= 500:
        return TransportError(f"Anthropic transport failed: {type(exc).__name__}")
    if type(exc).__name__ in {"APIConnectionError", "APITimeoutError"}:
        return TransportError(f"Anthropic transport failed: {type(exc).__name__}")
    return LLMError(f"Anthropic request failed: {type(exc).__name__}")
