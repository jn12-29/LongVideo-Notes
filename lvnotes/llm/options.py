from dataclasses import replace

from lvnotes.core.config import LLMProfile
from lvnotes.core.exceptions import LLMError
from lvnotes.llm.types import LLMRequestOptions


def apply_profile_defaults(profile: LLMProfile, options: LLMRequestOptions) -> LLMRequestOptions:
    return replace(
        options,
        reasoning_effort=options.reasoning_effort if options.reasoning_effort is not None else profile.reasoning_effort,
        thinking_budget_tokens=options.thinking_budget_tokens if options.thinking_budget_tokens is not None else profile.thinking_budget_tokens,
    )


def validate_reasoning_options(profile: LLMProfile, options: LLMRequestOptions) -> None:
    if options.reasoning_effort is not None and options.reasoning_effort not in {"minimal", "low", "medium", "high"}:
        raise LLMError("reasoning_effort must be one of: minimal, low, medium, high")
    if options.thinking_budget_tokens is not None and options.thinking_budget_tokens <= 0:
        raise LLMError("thinking_budget_tokens must be positive")
    if (options.reasoning_effort is not None or options.thinking_budget_tokens is not None) and "reasoning" not in profile.capabilities:
        raise LLMError(f"LLM profile '{profile.name}' does not support reasoning")
    if options.reasoning_effort is not None and profile.provider == "anthropic_messages":
        raise LLMError("anthropic_messages uses thinking_budget_tokens, not reasoning_effort")
    if options.thinking_budget_tokens is not None and profile.provider in {"openai_chat", "openai_responses", "openai_compatible_chat"}:
        raise LLMError(f"{profile.provider} uses reasoning_effort, not thinking_budget_tokens")
