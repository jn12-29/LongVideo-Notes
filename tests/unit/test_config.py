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
    assert config.visual_pipeline.filter.detector == "content"
    assert config.visual_pipeline.filter.threshold == "auto"
    assert config.visual_pipeline.filter.auto_threshold_candidates == (10.0, 15.0, 20.0, 25.0, 27.0, 30.0, 35.0, 40.0)
    assert config.visual_pipeline.filter.target_frames_per_minute == 1.5
    assert config.visual_pipeline.filter.min_scene_len_seconds == 1.0
    assert config.visual_pipeline.filter.representative == "content"
    assert config.visual_pipeline.filter.candidate_fps == 3.0
    assert config.visual_pipeline.filter.min_content_score == 0.5
    assert config.visual_pipeline.filter.duplicate_pixel_mean_threshold == 0.025
    assert config.visual_pipeline.align.max_context_gap_seconds == 3.0
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


def test_visual_align_max_context_gap_seconds_must_be_non_negative() -> None:
    payload = _minimal_config()
    payload["visual_pipeline"] = {"align": {"max_context_gap_seconds": -1}}

    with pytest.raises(ValueError, match="max_context_gap_seconds"):
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


def test_visual_filter_config_validates_scene_detector_options() -> None:
    payload = _minimal_config()
    payload["visual_pipeline"] = {
        "filter": {
            "detector": "content",
            "threshold": 30.0,
            "auto_threshold_candidates": [12.0, 18.0, 24.0],
            "target_frames_per_minute": 1.5,
            "min_scene_len_seconds": 2.0,
            "representative": "middle",
            "candidate_fps": 5.0,
            "min_content_score": 0.7,
            "duplicate_pixel_mean_threshold": 0.05,
        }
    }

    config = AppConfig.model_validate(payload)

    assert config.visual_pipeline.filter.threshold == 30.0
    assert config.visual_pipeline.filter.auto_threshold_candidates == (12.0, 18.0, 24.0)
    assert config.visual_pipeline.filter.target_frames_per_minute == 1.5
    assert config.visual_pipeline.filter.min_scene_len_seconds == 2.0
    assert config.visual_pipeline.filter.representative == "middle"
    assert config.visual_pipeline.filter.candidate_fps == 5.0
    assert config.visual_pipeline.filter.min_content_score == 0.7
    assert config.visual_pipeline.filter.duplicate_pixel_mean_threshold == 0.05


@pytest.mark.parametrize(
    "filter_config",
    [
        {"detector": "adaptive"},
        {"threshold": 0},
        {"threshold": "adaptive"},
        {"auto_threshold_candidates": []},
        {"auto_threshold_candidates": [10.0, 0]},
        {"target_frames_per_minute": 0},
        {"min_scene_len_seconds": 0},
        {"representative": "end"},
        {"candidate_fps": 0},
        {"min_content_score": 0},
        {"duplicate_pixel_mean_threshold": 0},
    ],
)
def test_visual_filter_config_rejects_invalid_scene_detector_options(filter_config: dict[str, object]) -> None:
    payload = _minimal_config()
    payload["visual_pipeline"] = {"filter": filter_config}

    with pytest.raises(ValueError):
        AppConfig.model_validate(payload)


@pytest.mark.parametrize("field", ["threshold", "target_frames_per_minute", "min_scene_len_seconds", "candidate_fps", "min_content_score", "duplicate_pixel_mean_threshold"])
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_visual_filter_config_rejects_non_finite_float_options(field: str, value: float) -> None:
    payload = _minimal_config()
    payload["visual_pipeline"] = {"filter": {field: value}}

    with pytest.raises(ValueError):
        AppConfig.model_validate(payload)


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_visual_filter_config_rejects_non_finite_auto_threshold_candidates(value: float) -> None:
    payload = _minimal_config()
    payload["visual_pipeline"] = {"filter": {"auto_threshold_candidates": [10.0, value]}}

    with pytest.raises(ValueError):
        AppConfig.model_validate(payload)


def test_legacy_filter_variant_config_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    payload = _minimal_config()
    payload["visual_pipeline"] = {"filter": {"variants_file": "variants.yaml"}}
    config_path.write_text(__import__("yaml").safe_dump(payload), encoding="utf-8")

    with pytest.raises(ConfigError, match="variants_file"):
        load_config(config_path)


def test_legacy_visual_sample_config_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    payload = _minimal_config()
    payload["visual_pipeline"] = {"sample": {"fps": 1}}
    config_path.write_text(__import__("yaml").safe_dump(payload), encoding="utf-8")

    with pytest.raises(ConfigError, match="sample"):
        load_config(config_path)
