import base64
import os

from lvnotes.core.config import LLMProfile
from lvnotes.core.exceptions import AuthError, LLMError, RateLimitError, TransportError
from lvnotes.llm.options import apply_profile_defaults, validate_reasoning_options
from lvnotes.llm.types import ImagePart, LLMMessage, LLMRequestOptions, LLMTextResult, LLMUsage, TextPart


class OpenAIChatClient:
    def __init__(self, profile: LLMProfile, api_key: str | None) -> None:
        self._profile = profile
        self._client = _create_openai_client(profile, api_key)

    @property
    def profile(self) -> LLMProfile:
        return self._profile

    def complete(self, messages: list[LLMMessage], options: LLMRequestOptions | None = None) -> LLMTextResult:
        request_options = apply_profile_defaults(self._profile, options or LLMRequestOptions())
        validate_reasoning_options(self._profile, request_options)
        request_payload = {
            "model": self._profile.model,
            "messages": _chat_messages(messages),
            "temperature": request_options.temperature,
            "max_tokens": request_options.max_output_tokens,
            "timeout": request_options.timeout_seconds or self._profile.timeout_seconds,
            "response_format": {"type": "json_object"} if request_options.json_mode else None,
        }
        if request_options.reasoning_effort is not None:
            request_payload["reasoning_effort"] = request_options.reasoning_effort
        try:
            response = self._client.chat.completions.create(**request_payload)
        except Exception as exc:
            raise _normalize_openai_error(exc) from exc
        choice = response.choices[0]
        text = choice.message.content or ""
        return LLMTextResult(text=text, model=response.model, usage=_usage(response.usage), raw_response_id=response.id)


def _create_openai_client(profile: LLMProfile, api_key: str | None) -> object:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LLMError("openai package is required for OpenAI providers") from exc
    try:
        return OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY") or "", base_url=profile.base_url)
    except Exception as exc:
        raise _normalize_openai_error(exc) from exc


def _chat_messages(messages: list[LLMMessage]) -> list[dict[str, object]]:
    return [{"role": message.role, "content": _content_parts(message.content)} for message in messages]


def _content_parts(parts: list[TextPart | ImagePart]) -> str | list[dict[str, object]]:
    if all(isinstance(part, TextPart) for part in parts):
        return "\n".join(part.text for part in parts if isinstance(part, TextPart))
    content: list[dict[str, object]] = []
    for part in parts:
        if isinstance(part, TextPart):
            content.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            content.append({"type": "image_url", "image_url": {"url": _image_data_url(part)}})
    return content


def _image_data_url(part: ImagePart) -> str:
    encoded = base64.b64encode(part.path.read_bytes()).decode("ascii")
    return f"data:{part.mime_type};base64,{encoded}"


def _usage(usage: object) -> LLMUsage | None:
    if usage is None:
        return None
    input_tokens = _int_attr(usage, "prompt_tokens")
    output_tokens = _int_attr(usage, "completion_tokens")
    return LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=_int_attr(usage, "total_tokens"))


def _int_attr(source: object, name: str) -> int | None:
    value = getattr(source, name, None)
    return value if isinstance(value, int) else None


def _normalize_openai_error(exc: Exception) -> LLMError:
    status_code = getattr(exc, "status_code", None)
    if status_code in {401, 403}:
        return AuthError(f"OpenAI authentication failed: {type(exc).__name__}")
    if status_code == 429:
        return RateLimitError(f"OpenAI rate limit reached: {type(exc).__name__}")
    if isinstance(status_code, int) and status_code >= 500:
        return TransportError(f"OpenAI transport failed: {type(exc).__name__}")
    if type(exc).__name__ in {"APIConnectionError", "APITimeoutError"}:
        return TransportError(f"OpenAI transport failed: {type(exc).__name__}")
    return LLMError(f"OpenAI request failed: {type(exc).__name__}")
