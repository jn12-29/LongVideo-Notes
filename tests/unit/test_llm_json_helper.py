from dataclasses import dataclass

import pytest

from lvnotes.core.config import LLMProfile
from lvnotes.core.exceptions import LLMError
from lvnotes.llm.base import LLMClient
from lvnotes.llm.json_helper import complete_json
from lvnotes.llm.text_helper import complete_text
from lvnotes.llm.types import LLMMessage, LLMRequestOptions, LLMTextResult, LLMUsage, TextPart


@dataclass(frozen=True)
class JsonResult:
    title: str
    count: int


class StaticClient:
    def __init__(self, text: str, profile: LLMProfile | None = None) -> None:
        self._text = text
        self.options: LLMRequestOptions | None = None
        self._profile = profile or LLMProfile(
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
        self.options = options
        return LLMTextResult(text=self._text, model="test", usage=LLMUsage(None, None, None))


def test_complete_json_builds_dataclass() -> None:
    client: LLMClient = StaticClient('{"title":"hello","count":2}')
    result = complete_json(client, [LLMMessage(role="user", content=[TextPart("x")])], JsonResult)

    assert result == JsonResult(title="hello", count=2)


def test_complete_text_applies_profile_reasoning_defaults() -> None:
    profile = LLMProfile(
        name="reasoning",
        provider="openai_compatible_chat",
        base_url="http://localhost:8000/v1",
        api_key_env=None,
        model="test",
        capabilities=frozenset({"reasoning"}),
        reasoning_effort="medium",
    )
    client = StaticClient("ok", profile)

    complete_text(client, [LLMMessage(role="user", content=[TextPart("x")])])

    assert client.options is not None
    assert client.options.reasoning_effort == "medium"


def test_complete_text_rejects_reasoning_without_capability() -> None:
    client = StaticClient("ok")

    with pytest.raises(LLMError, match="reasoning"):
        complete_text(
            client,
            [LLMMessage(role="user", content=[TextPart("x")])],
            LLMRequestOptions(reasoning_effort="medium"),
        )
