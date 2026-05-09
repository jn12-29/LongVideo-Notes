import time

from lvnotes.core.exceptions import LLMError, RateLimitError
from lvnotes.llm.base import LLMClient
from lvnotes.llm.budget import check_context_budget
from lvnotes.llm.options import apply_profile_defaults, validate_reasoning_options
from lvnotes.llm.rate_limit import acquire_profile_rate_limit
from lvnotes.llm.types import ImagePart, LLMMessage, LLMRequestOptions, LLMTextResult

_RATE_LIMIT_RETRY_DELAYS = (5.0, 10.0, 20.0)


def complete_text(
    client: LLMClient,
    messages: list[LLMMessage],
    options: LLMRequestOptions | None = None,
) -> LLMTextResult:
    request_options = apply_profile_defaults(client.profile, options or LLMRequestOptions())
    estimate = check_context_budget(client.profile, messages, request_options.max_output_tokens)
    _check_capabilities(client, messages, request_options)
    return _complete_with_rate_limit_retry(client, messages, request_options, estimate.total_tokens)


def _complete_with_rate_limit_retry(
    client: LLMClient,
    messages: list[LLMMessage],
    options: LLMRequestOptions,
    token_budget: int,
) -> LLMTextResult:
    for delay in (0.0, *_RATE_LIMIT_RETRY_DELAYS):
        if delay > 0:
            time.sleep(delay)
        acquire_profile_rate_limit(client.profile, token_budget)
        try:
            return client.complete(messages, options)
        except RateLimitError:
            if delay == _RATE_LIMIT_RETRY_DELAYS[-1]:
                raise
    raise AssertionError("unreachable rate limit retry state")


def _check_capabilities(client: LLMClient, messages: list[LLMMessage], options: LLMRequestOptions) -> None:
    capabilities = client.profile.capabilities
    if options.json_mode and "json_mode" not in capabilities:
        raise LLMError(f"LLM profile '{client.profile.name}' does not support json_mode")
    validate_reasoning_options(client.profile, options)
    if "vision" not in capabilities:
        for message in messages:
            if any(isinstance(part, ImagePart) for part in message.content):
                raise LLMError(f"LLM profile '{client.profile.name}' does not support vision")
