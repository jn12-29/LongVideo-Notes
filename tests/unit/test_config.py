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
