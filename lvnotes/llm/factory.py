import os
from urllib.parse import urlparse

from lvnotes.core.config import AppConfig, LLMProfile
from lvnotes.core.exceptions import AuthError, ConfigError
from lvnotes.llm.anthropic_messages import AnthropicMessagesClient
from lvnotes.llm.base import LLMClient
from lvnotes.llm.openai_chat import OpenAIChatClient
from lvnotes.llm.openai_compatible_chat import OpenAICompatibleChatClient
from lvnotes.llm.openai_responses import OpenAIResponsesClient


def get_client(config: AppConfig, profile_name: str) -> LLMClient:
    try:
        profile = config.llm.profiles[profile_name]
    except KeyError as exc:
        raise ConfigError(f"unknown LLM profile: {profile_name}") from exc
    api_key = _resolve_api_key(profile_name, profile)
    match profile.provider:
        case "openai_chat":
            return OpenAIChatClient(profile, api_key)
        case "openai_responses":
            return OpenAIResponsesClient(profile, api_key)
        case "anthropic_messages":
            if api_key is None:
                raise ConfigError(f"LLM profile '{profile_name}' requires api_key_env")
            return AnthropicMessagesClient(profile, api_key)
        case "openai_compatible_chat":
            return OpenAICompatibleChatClient(profile, api_key)
        case _:
            raise ConfigError(f"unknown LLM provider: {profile.provider}")


def for_task(config: AppConfig, task_name: str) -> LLMClient:
    try:
        profile_name = config.tasks[task_name]
    except KeyError as exc:
        raise ConfigError(f"unknown LLM task: {task_name}") from exc
    return get_client(config, profile_name)


def _resolve_api_key(profile_name: str, profile: LLMProfile) -> str | None:
    if profile.api_key_env is None:
        if profile.provider == "openai_compatible_chat" and _is_local_endpoint(profile.base_url):
            return None
        raise ConfigError(f"LLM profile '{profile_name}' requires api_key_env")
    value = os.environ.get(profile.api_key_env)
    if value is None or value == "":
        raise AuthError(f"LLM profile '{profile_name}' requires env var {profile.api_key_env}")
    return value


def _is_local_endpoint(base_url: str | None) -> bool:
    if base_url is None:
        return False
    parsed = urlparse(base_url)
    return parsed.hostname in {"localhost", "127.0.0.1", "::1"}
