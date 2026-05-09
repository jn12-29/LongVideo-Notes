from pathlib import Path

import pytest

from lvnotes.core.config import AppConfig, load_config
from lvnotes.core.exceptions import ConfigError


def _minimal_config() -> dict[str, object]:
    return {
        "llm": {
            "profiles": {
                "main": {
                    "provider": "openai_responses",
                    "base_url": "https://api.openai.com/v1",
                    "api_key_env": "OPENAI_API_KEY",
                    "model": "gpt-5",
                    "capabilities": ["json_mode"],
                },
                "vlm": {
                    "provider": "openai_compatible_chat",
                    "base_url": "https://example.com/v1",
                    "api_key_env": "OPENROUTER_API_KEY",
                    "model": "vlm",
                    "capabilities": ["vision"],
                },
            }
        },
        "tasks": {
            "segment": "main",
            "refine": "main",
            "outline": "main",
            "section": "main",
            "slide_judge": "vlm",
            "slide_describe": "main",
        },
    }


def test_app_config_validates_minimal_config() -> None:
    config = AppConfig.model_validate(_minimal_config())

    assert config.llm.profiles["main"].name == "main"
    assert config.audio_pipeline.extract.sample_rate == 16000
    assert config.audio_pipeline.refine.mode == "adaptive"
    assert config.audio_pipeline.refine.batch_size == 8


def test_refine_config_validates_mode_and_batch_size() -> None:
    payload = _minimal_config()
    payload["audio_pipeline"] = {"refine": {"mode": "invalid", "batch_size": 0}}

    with pytest.raises(ValueError):
        AppConfig.model_validate(payload)


def test_reasoning_options_require_reasoning_capability() -> None:
    payload = _minimal_config()
    profiles = payload["llm"]["profiles"]  # type: ignore[index]
    profiles["main"]["reasoning_effort"] = "medium"  # type: ignore[index]

    with pytest.raises(ValueError, match="reasoning capability"):
        AppConfig.model_validate(payload)


def test_reasoning_options_are_loaded_when_capability_is_declared() -> None:
    payload = _minimal_config()
    profiles = payload["llm"]["profiles"]  # type: ignore[index]
    profiles["main"]["capabilities"] = ["json_mode", "reasoning"]  # type: ignore[index]
    profiles["main"]["reasoning_effort"] = "high"  # type: ignore[index]

    config = AppConfig.model_validate(payload)

    assert config.llm.profiles["main"].reasoning_effort == "high"


def test_provider_specific_reasoning_options_are_validated() -> None:
    payload = _minimal_config()
    profiles = payload["llm"]["profiles"]  # type: ignore[index]
    profiles["main"]["provider"] = "anthropic_messages"  # type: ignore[index]
    profiles["main"]["capabilities"] = ["json_mode", "reasoning"]  # type: ignore[index]
    profiles["main"]["reasoning_effort"] = "medium"  # type: ignore[index]

    with pytest.raises(ValueError, match="thinking_budget_tokens"):
        AppConfig.model_validate(payload)


def test_unknown_task_is_rejected() -> None:
    payload = _minimal_config()
    tasks = dict(payload["tasks"])  # type: ignore[arg-type]
    tasks["unknown"] = "main"
    payload["tasks"] = tasks

    with pytest.raises(ValueError):
        AppConfig.model_validate(payload)


def test_load_config_missing_file_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        load_config(Path("does-not-exist.yaml"))
