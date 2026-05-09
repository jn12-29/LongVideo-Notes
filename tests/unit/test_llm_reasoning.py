import pytest

from lvnotes.core.config import LLMProfile
from lvnotes.core.exceptions import LLMError
from lvnotes.llm.anthropic_messages import AnthropicMessagesClient
from lvnotes.llm.openai_chat import OpenAIChatClient
from lvnotes.llm.openai_responses import OpenAIResponsesClient
from lvnotes.llm.types import LLMMessage, LLMRequestOptions, TextPart


class FakeUsage:
    prompt_tokens = 1
    completion_tokens = 2
    total_tokens = 3
    input_tokens = 1
    output_tokens = 2


class FakeChatChoiceMessage:
    content = "ok"


class FakeChatChoice:
    message = FakeChatChoiceMessage()


class FakeChatResponse:
    choices = [FakeChatChoice()]
    model = "test"
    usage = FakeUsage()
    id = "chat-response"


class FakeResponsesResponse:
    output_text = "ok"
    usage = FakeUsage()
    id = "responses-response"


class FakeAnthropicTextBlock:
    text = "ok"


class FakeAnthropicResponse:
    content = [FakeAnthropicTextBlock()]
    model = "test"
    usage = FakeUsage()
    id = "anthropic-response"


class FakeCreate:
    def __init__(self, response: object) -> None:
        self.response = response
        self.kwargs: dict[str, object] | None = None

    def create(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return self.response


class FakeOpenAIChatClient:
    def __init__(self) -> None:
        self.create = FakeCreate(FakeChatResponse())
        self.chat = type("Chat", (), {"completions": self.create})()


class FakeOpenAIResponsesClient:
    def __init__(self) -> None:
        self.create = FakeCreate(FakeResponsesResponse())
        self.responses = self.create


class FakeAnthropicSDKClient:
    def __init__(self) -> None:
        self.create = FakeCreate(FakeAnthropicResponse())
        self.messages = self.create


def _profile(provider: str) -> LLMProfile:
    return LLMProfile(
        name="test",
        provider=provider,
        base_url="http://localhost:8000/v1",
        api_key_env=None,
        model="test",
        capabilities=frozenset({"reasoning"}),
    )


def _profile_with_reasoning_effort(provider: str, effort: str = "medium") -> LLMProfile:
    return _profile(provider).model_copy(update={"reasoning_effort": effort})


def _profile_with_thinking_budget(budget: int = 4096) -> LLMProfile:
    return _profile("anthropic_messages").model_copy(update={"thinking_budget_tokens": budget})


def test_openai_chat_passes_reasoning_effort() -> None:
    sdk = FakeOpenAIChatClient()
    client = OpenAIChatClient.__new__(OpenAIChatClient)
    client._profile = _profile("openai_chat")
    client._client = sdk

    client.complete([LLMMessage(role="user", content=[TextPart("x")])], LLMRequestOptions(reasoning_effort="high"))

    assert sdk.create.kwargs is not None
    assert sdk.create.kwargs["reasoning_effort"] == "high"


def test_openai_chat_applies_profile_reasoning_effort() -> None:
    sdk = FakeOpenAIChatClient()
    client = OpenAIChatClient.__new__(OpenAIChatClient)
    client._profile = _profile_with_reasoning_effort("openai_chat", "medium")
    client._client = sdk

    client.complete([LLMMessage(role="user", content=[TextPart("x")])])

    assert sdk.create.kwargs is not None
    assert sdk.create.kwargs["reasoning_effort"] == "medium"


def test_openai_responses_passes_reasoning_effort() -> None:
    sdk = FakeOpenAIResponsesClient()
    client = OpenAIResponsesClient.__new__(OpenAIResponsesClient)
    client._profile = _profile("openai_responses")
    client._client = sdk

    client.complete([LLMMessage(role="user", content=[TextPart("x")])], LLMRequestOptions(reasoning_effort="medium"))

    assert sdk.create.kwargs is not None
    assert sdk.create.kwargs["reasoning"] == {"effort": "medium"}


def test_anthropic_messages_passes_thinking_budget() -> None:
    sdk = FakeAnthropicSDKClient()
    client = AnthropicMessagesClient.__new__(AnthropicMessagesClient)
    client._profile = _profile("anthropic_messages")
    client._client = sdk

    client.complete(
        [LLMMessage(role="user", content=[TextPart("x")])],
        LLMRequestOptions(max_output_tokens=8192, thinking_budget_tokens=4096),
    )

    assert sdk.create.kwargs is not None
    assert sdk.create.kwargs["max_tokens"] == 8192
    assert sdk.create.kwargs["thinking"] == {"type": "enabled", "budget_tokens": 4096}


def test_anthropic_messages_applies_profile_thinking_budget() -> None:
    sdk = FakeAnthropicSDKClient()
    client = AnthropicMessagesClient.__new__(AnthropicMessagesClient)
    client._profile = _profile_with_thinking_budget(4096)
    client._client = sdk

    client.complete([LLMMessage(role="user", content=[TextPart("x")])])

    assert sdk.create.kwargs is not None
    assert sdk.create.kwargs["max_tokens"] == 5120
    assert sdk.create.kwargs["thinking"] == {"type": "enabled", "budget_tokens": 4096}


def test_anthropic_messages_rejects_thinking_budget_greater_than_max_tokens() -> None:
    sdk = FakeAnthropicSDKClient()
    client = AnthropicMessagesClient.__new__(AnthropicMessagesClient)
    client._profile = _profile("anthropic_messages")
    client._client = sdk

    with pytest.raises(LLMError, match="less than max_output_tokens"):
        client.complete(
            [LLMMessage(role="user", content=[TextPart("x")])],
            LLMRequestOptions(max_output_tokens=1024, thinking_budget_tokens=4096),
        )

    assert sdk.create.kwargs is None


def test_anthropic_messages_rejects_zero_max_output_tokens_with_thinking_budget() -> None:
    sdk = FakeAnthropicSDKClient()
    client = AnthropicMessagesClient.__new__(AnthropicMessagesClient)
    client._profile = _profile("anthropic_messages")
    client._client = sdk

    with pytest.raises(LLMError, match="less than max_output_tokens"):
        client.complete(
            [LLMMessage(role="user", content=[TextPart("x")])],
            LLMRequestOptions(max_output_tokens=0, thinking_budget_tokens=1),
        )

    assert sdk.create.kwargs is None


def test_anthropic_messages_rejects_non_positive_thinking_budget_before_sdk_call() -> None:
    sdk = FakeAnthropicSDKClient()
    client = AnthropicMessagesClient.__new__(AnthropicMessagesClient)
    client._profile = _profile("anthropic_messages")
    client._client = sdk

    with pytest.raises(LLMError, match="thinking_budget_tokens must be positive"):
        client.complete([LLMMessage(role="user", content=[TextPart("x")])], LLMRequestOptions(thinking_budget_tokens=0))

    assert sdk.create.kwargs is None


def test_openai_chat_rejects_thinking_budget_before_sdk_call() -> None:
    sdk = FakeOpenAIChatClient()
    client = OpenAIChatClient.__new__(OpenAIChatClient)
    client._profile = _profile("openai_chat")
    client._client = sdk

    with pytest.raises(LLMError, match="thinking_budget_tokens"):
        client.complete([LLMMessage(role="user", content=[TextPart("x")])], LLMRequestOptions(thinking_budget_tokens=4096))

    assert sdk.create.kwargs is None


def test_openai_chat_rejects_invalid_reasoning_effort_before_sdk_call() -> None:
    sdk = FakeOpenAIChatClient()
    client = OpenAIChatClient.__new__(OpenAIChatClient)
    client._profile = _profile("openai_chat")
    client._client = sdk

    with pytest.raises(LLMError, match="reasoning_effort"):
        client.complete([LLMMessage(role="user", content=[TextPart("x")])], LLMRequestOptions(reasoning_effort="invalid"))

    assert sdk.create.kwargs is None


def test_anthropic_messages_rejects_reasoning_effort_before_sdk_call() -> None:
    sdk = FakeAnthropicSDKClient()
    client = AnthropicMessagesClient.__new__(AnthropicMessagesClient)
    client._profile = _profile("anthropic_messages")
    client._client = sdk

    with pytest.raises(LLMError, match="thinking_budget_tokens"):
        client.complete([LLMMessage(role="user", content=[TextPart("x")])], LLMRequestOptions(reasoning_effort="high"))

    assert sdk.create.kwargs is None
