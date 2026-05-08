from dataclasses import dataclass

from lvnotes.core.config import LLMProfile
from lvnotes.llm.base import LLMClient
from lvnotes.llm.json_helper import complete_json
from lvnotes.llm.types import LLMMessage, LLMRequestOptions, LLMTextResult, LLMUsage, TextPart


@dataclass(frozen=True)
class JsonResult:
    title: str
    count: int


class StaticClient:
    def __init__(self, text: str) -> None:
        self._text = text
        self._profile = LLMProfile(
            name="test",
            provider="openai_compatible_chat",
            base_url="http://localhost:8000/v1",
            api_key_env=None,
            model="test",
            capabilities=frozenset({"json_mode"}),
        )

    @property
    def profile(self) -> LLMProfile:
        return self._profile

    def complete(
        self,
        messages: list[LLMMessage],
        options: LLMRequestOptions | None = None,
    ) -> LLMTextResult:
        return LLMTextResult(text=self._text, model="test", usage=LLMUsage(None, None, None))


def test_complete_json_builds_dataclass() -> None:
    client: LLMClient = StaticClient('{"title":"hello","count":2}')
    result = complete_json(client, [LLMMessage(role="user", content=[TextPart("x")])], JsonResult)

    assert result == JsonResult(title="hello", count=2)
