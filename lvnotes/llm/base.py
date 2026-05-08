from typing import Protocol

from lvnotes.core.config import LLMProfile
from lvnotes.llm.types import LLMMessage, LLMRequestOptions, LLMTextResult


class LLMClient(Protocol):
    @property
    def profile(self) -> LLMProfile: ...

    def complete(self, messages: list[LLMMessage], options: LLMRequestOptions | None = None) -> LLMTextResult: ...
