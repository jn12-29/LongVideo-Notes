from lvnotes.core.config import LLMProfile
from lvnotes.core.exceptions import LLMError
from lvnotes.llm.openai_chat import _create_openai_client, _image_data_url, _normalize_openai_error, _usage
from lvnotes.llm.options import apply_profile_defaults, validate_reasoning_options
from lvnotes.llm.types import ImagePart, LLMMessage, LLMRequestOptions, LLMTextResult, TextPart


class OpenAIResponsesClient:
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
            "input": _responses_input(messages),
            "temperature": request_options.temperature,
            "max_output_tokens": request_options.max_output_tokens,
            "timeout": request_options.timeout_seconds or self._profile.timeout_seconds,
            "text": {"format": {"type": "json_object"}} if request_options.json_mode else None,
        }
        if request_options.reasoning_effort is not None:
            request_payload["reasoning"] = {"effort": request_options.reasoning_effort}
        try:
            response = self._client.responses.create(**request_payload)
        except Exception as exc:
            raise _normalize_openai_error(exc) from exc
        text = getattr(response, "output_text", None)
        if not isinstance(text, str):
            raise LLMError("OpenAI Responses API returned no text output")
        return LLMTextResult(text=text, model=self._profile.model, usage=_usage(getattr(response, "usage", None)), raw_response_id=response.id)


def _responses_input(messages: list[LLMMessage]) -> list[dict[str, object]]:
    return [{"role": message.role, "content": _responses_content(message.content)} for message in messages]


def _responses_content(parts: list[TextPart | ImagePart]) -> list[dict[str, object]]:
    content: list[dict[str, object]] = []
    for part in parts:
        if isinstance(part, TextPart):
            content.append({"type": "input_text", "text": part.text})
        elif isinstance(part, ImagePart):
            content.append({"type": "input_image", "image_url": _image_data_url(part)})
    return content
