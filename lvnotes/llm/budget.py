from dataclasses import dataclass

from lvnotes.core.config import LLMProfile
from lvnotes.core.exceptions import ContextLengthError
from lvnotes.llm.types import ImagePart, LLMMessage, TextPart


@dataclass(frozen=True)
class TokenEstimate:
    input_tokens: int
    max_output_tokens: int | None
    total_tokens: int


def estimate_messages_tokens(messages: list[LLMMessage]) -> int:
    character_count = 0
    image_count = 0
    for message in messages:
        character_count += len(message.role)
        for part in message.content:
            if isinstance(part, TextPart):
                character_count += len(part.text)
            elif isinstance(part, ImagePart):
                image_count += 1
    return character_count // 4 + image_count * 1024


def check_context_budget(
    profile: LLMProfile,
    messages: list[LLMMessage],
    max_output_tokens: int | None,
) -> TokenEstimate:
    input_tokens = estimate_messages_tokens(messages)
    total_tokens = input_tokens + (max_output_tokens or 0)
    estimate = TokenEstimate(input_tokens=input_tokens, max_output_tokens=max_output_tokens, total_tokens=total_tokens)
    if profile.max_context is not None and total_tokens > profile.max_context:
        raise ContextLengthError(
            f"LLM profile '{profile.name}' context budget exceeded: {total_tokens} > {profile.max_context}"
        )
    return estimate
