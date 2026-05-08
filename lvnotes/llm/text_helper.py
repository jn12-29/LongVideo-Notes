from lvnotes.core.exceptions import LLMError
from lvnotes.llm.base import LLMClient
from lvnotes.llm.budget import check_context_budget
from lvnotes.llm.types import ImagePart, LLMMessage, LLMRequestOptions, LLMTextResult


def complete_text(
    client: LLMClient,
    messages: list[LLMMessage],
    options: LLMRequestOptions | None = None,
) -> LLMTextResult:
    request_options = options or LLMRequestOptions()
    check_context_budget(client.profile, messages, request_options.max_output_tokens)
    _check_capabilities(client, messages, request_options)
    return client.complete(messages, request_options)


def _check_capabilities(client: LLMClient, messages: list[LLMMessage], options: LLMRequestOptions) -> None:
    capabilities = client.profile.capabilities
    if options.json_mode and "json_mode" not in capabilities:
        raise LLMError(f"LLM profile '{client.profile.name}' does not support json_mode")
    if "vision" not in capabilities:
        for message in messages:
            if any(isinstance(part, ImagePart) for part in message.content):
                raise LLMError(f"LLM profile '{client.profile.name}' does not support vision")
