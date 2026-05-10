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
                    "rpm_limit": 60,
                    "tpm_limit": 90000,
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
    assert config.llm.profiles["main"].rpm_limit == 60
    assert config.llm.profiles["main"].tpm_limit == 90000
    assert config.audio_pipeline.extract.sample_rate == 16000
    assert config.audio_pipeline.refine.mode == "adaptive"
    assert config.audio_pipeline.refine.batch_size == 8
    assert config.visual_pipeline.describe.concurrent_calls == 5


@pytest.mark.parametrize("field", ["rpm_limit", "tpm_limit"])
@pytest.mark.parametrize("value", [0, -1])
def test_rate_limits_must_be_positive(field: str, value: int) -> None:
    payload = _minimal_config()
    profiles = payload["llm"]["profiles"]  # type: ignore[index]
    profiles["main"][field] = value  # type: ignore[index]

    with pytest.raises(ValueError, match=f"{field} must be positive"):
        AppConfig.model_validate(payload)


def test_rate_limits_may_be_null() -> None:
    payload = _minimal_config()
    profiles = payload["llm"]["profiles"]  # type: ignore[index]
    profiles["main"]["rpm_limit"] = None  # type: ignore[index]
    profiles["main"]["tpm_limit"] = None  # type: ignore[index]

    config = AppConfig.model_validate(payload)

    assert config.llm.profiles["main"].rpm_limit is None
    assert config.llm.profiles["main"].tpm_limit is None


def test_refine_config_validates_mode_and_batch_size() -> None:
    payload = _minimal_config()
    payload["audio_pipeline"] = {"refine": {"mode": "invalid", "batch_size": 0}}

    with pytest.raises(ValueError):
        AppConfig.model_validate(payload)


def test_visual_describe_concurrent_calls_must_be_positive() -> None:
    payload = _minimal_config()
    payload["visual_pipeline"] = {"describe": {"concurrent_calls": 0}}

    with pytest.raises(ValueError, match="concurrent_calls"):
        AppConfig.model_validate(payload)


def test_merge_section_concurrent_calls_must_be_positive() -> None:
    payload = _minimal_config()
    payload["merge"] = {"section": {"concurrent_calls": 0}}

    with pytest.raises(ValueError, match="concurrent_calls"):
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


def test_load_config_loads_filter_variants_file_relative_to_config(tmp_path: Path) -> None:
    variants = tmp_path / "variants.yaml"
    variants.write_text(
        """
variants:
  - name: default
    phash_threshold: 7
""".lstrip(),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    payload = _minimal_config()
    payload["visual_pipeline"] = {"filter": {"variants_file": "variants.yaml"}}
    config_path.write_text(__import__("yaml").safe_dump(payload), encoding="utf-8")

    config = load_config(config_path)

    assert config.visual_pipeline.filter.variants_file == variants
    assert config.filter_variants is not None
    assert config.filter_variants.variants[0].name == "default"
    assert config.filter_variants.variants[0].phash_threshold == 7


def test_filter_variant_names_must_be_unique() -> None:
    payload = _minimal_config()
    payload["filter_variants"] = {"variants": [{"name": "same"}, {"name": "same"}]}

    with pytest.raises(ValueError, match="unique"):
        AppConfig.model_validate(payload)


def test_filter_variant_name_must_be_safe() -> None:
    payload = _minimal_config()
    payload["filter_variants"] = {"variants": [{"name": "bad/name"}]}

    with pytest.raises(ValueError, match="variant name"):
        AppConfig.model_validate(payload)


def test_active_filter_variant_must_exist() -> None:
    payload = _minimal_config()
    payload["visual_pipeline"] = {"filter": {"active_variant": "missing"}}
    payload["filter_variants"] = {"variants": [{"name": "default"}]}

    with pytest.raises(ValueError, match="active_variant"):
        AppConfig.model_validate(payload)


def test_active_filter_variant_must_exist_in_loaded_variants_file(tmp_path: Path) -> None:
    variants = tmp_path / "variants.yaml"
    variants.write_text("variants:\n  - name: default\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    payload = _minimal_config()
    payload["visual_pipeline"] = {"filter": {"active_variant": "missing", "variants_file": "variants.yaml"}}
    config_path.write_text(__import__("yaml").safe_dump(payload), encoding="utf-8")

    with pytest.raises(ConfigError, match="active_variant"):
        load_config(config_path)
