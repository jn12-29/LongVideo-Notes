from lvnotes.core.config import LLMProfile
from lvnotes.llm.openai_chat import OpenAIChatClient


class OpenAICompatibleChatClient(OpenAIChatClient):
    def __init__(self, profile: LLMProfile, api_key: str | None) -> None:
        super().__init__(profile, api_key)
