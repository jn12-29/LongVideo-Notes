from dataclasses import dataclass

import pytest

from lvnotes.core.config import LLMProfile
from lvnotes.core.exceptions import LLMError, RateLimitError
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


class RateLimitedOnceClient(StaticClient):
    def __init__(self) -> None:
        super().__init__('{"title":"hello","count":2}')
        self.calls = 0

    def complete(
        self,
        messages: list[LLMMessage],
        options: LLMRequestOptions | None = None,
    ) -> LLMTextResult:
        self.calls += 1
        if self.calls == 1:
            raise RateLimitError("limited")
        return super().complete(messages, options)


class SequenceClient(StaticClient):
    def __init__(self, outputs: list[str]) -> None:
        super().__init__(outputs[-1])
        self.outputs = outputs
        self.calls = 0

    def complete(
        self,
        messages: list[LLMMessage],
        options: LLMRequestOptions | None = None,
    ) -> LLMTextResult:
        self.calls += 1
        return LLMTextResult(text=self.outputs.pop(0), model="test", usage=LLMUsage(None, None, None))


class AlwaysRateLimitedClient(StaticClient):
    def __init__(self) -> None:
        super().__init__("")
        self.calls = 0

    def complete(
        self,
        messages: list[LLMMessage],
        options: LLMRequestOptions | None = None,
    ) -> LLMTextResult:
        self.calls += 1
        raise RateLimitError("limited")


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


def test_complete_text_retries_rate_limit_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    profile = LLMProfile(
        name="retry_limited",
        provider="openai_compatible_chat",
        base_url="http://localhost:8000/v1",
        api_key_env=None,
        model="test",
        capabilities=frozenset({"json_mode"}),
        rpm_limit=100,
    )
    acquire_calls: list[tuple[str, int]] = []
    sleeps: list[float] = []
    monkeypatch.setattr("lvnotes.llm.text_helper.acquire_profile_rate_limit", lambda profile, token_budget: acquire_calls.append((profile.name, token_budget)))
    monkeypatch.setattr("lvnotes.llm.text_helper.time.sleep", sleeps.append)
    client = RateLimitedOnceClient()
    client._profile = profile

    result = complete_text(client, [LLMMessage(role="user", content=[TextPart("x")])])

    assert result.text == '{"title":"hello","count":2}'
    assert client.calls == 2
    assert len(acquire_calls) == 2
    assert [name for name, _ in acquire_calls] == ["retry_limited", "retry_limited"]
    assert sleeps == [5.0]


def test_complete_text_raises_after_rate_limit_retries_are_exhausted(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sleeps: list[float] = []
    monkeypatch.setattr("lvnotes.llm.text_helper.time.sleep", sleeps.append)
    client = AlwaysRateLimitedClient()

    with pytest.raises(RateLimitError):
        complete_text(client, [LLMMessage(role="user", content=[TextPart("x")])])

    assert client.calls == 4
    assert sleeps == [5.0, 10.0, 20.0]


def test_complete_json_repair_retry_goes_through_limiter(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    acquire_calls: list[tuple[str, int]] = []
    monkeypatch.setattr("lvnotes.llm.text_helper.acquire_profile_rate_limit", lambda profile, token_budget: acquire_calls.append((profile.name, token_budget)))
    client = SequenceClient(["not json", '{"title":"fixed","count":3}'])

    result = complete_json(client, [LLMMessage(role="user", content=[TextPart("x")])], JsonResult, max_repair_retries=1)

    assert result == JsonResult(title="fixed", count=3)
    assert client.calls == 2
    assert len(acquire_calls) == 2
