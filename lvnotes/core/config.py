from pathlib import Path
import math
import string

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lvnotes.core.constants import SUPPORTED_LLM_CAPABILITIES, SUPPORTED_LLM_PROVIDERS
from lvnotes.core.exceptions import ConfigError

LLM_TASK_NAMES = frozenset({"segment", "refine", "outline", "section", "slide_judge", "slide_describe"})


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)


class ProjectConfig(FrozenModel):
    cache_dir: Path = Path("./cache")
    output_dir: Path = Path("./output")


class LLMProfile(FrozenModel):
    name: str = ""
    provider: str
    base_url: str | None = None
    api_key_env: str | None = None
    model: str
    capabilities: frozenset[str] = Field(default_factory=frozenset)
    max_context: int | None = None
    timeout_seconds: float | None = None
    reasoning_effort: str | None = None
    thinking_budget_tokens: int | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if value not in SUPPORTED_LLM_PROVIDERS:
            raise ValueError(f"unsupported LLM provider: {value}")
        return value

    @field_validator("capabilities", mode="before")
    @classmethod
    def validate_capabilities(cls, value: object) -> frozenset[str]:
        capabilities = frozenset(value or [])
        unknown = capabilities - SUPPORTED_LLM_CAPABILITIES
        if unknown:
            raise ValueError(f"unsupported LLM capabilities: {sorted(unknown)}")
        return capabilities

    @field_validator("reasoning_effort")
    @classmethod
    def validate_reasoning_effort(cls, value: str | None) -> str | None:
        if value is not None and value not in {"minimal", "low", "medium", "high"}:
            raise ValueError("reasoning_effort must be one of: minimal, low, medium, high")
        return value

    @field_validator("thinking_budget_tokens")
    @classmethod
    def validate_thinking_budget_tokens(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("thinking_budget_tokens must be positive")
        return value

    @field_validator("rpm_limit", "tpm_limit")
    @classmethod
    def validate_rate_limit(cls, value: int | None, info) -> int | None:
        if value is not None and value <= 0:
            raise ValueError(f"{info.field_name} must be positive")
        return value

    @model_validator(mode="after")
    def validate_reasoning_capability(self) -> "LLMProfile":
        if (self.reasoning_effort is not None or self.thinking_budget_tokens is not None) and "reasoning" not in self.capabilities:
            raise ValueError("reasoning options require reasoning capability")
        if self.provider == "anthropic_messages" and self.reasoning_effort is not None:
            raise ValueError("anthropic_messages uses thinking_budget_tokens, not reasoning_effort")
        if self.provider in {"openai_chat", "openai_responses", "openai_compatible_chat"} and self.thinking_budget_tokens is not None:
            raise ValueError(f"{self.provider} uses reasoning_effort, not thinking_budget_tokens")
        return self


class LLMConfig(FrozenModel):
    profiles: dict[str, LLMProfile]

    @model_validator(mode="after")
    def attach_profile_names(self) -> "LLMConfig":
        profiles = {name: profile.model_copy(update={"name": name}) for name, profile in self.profiles.items()}
        object.__setattr__(self, "profiles", profiles)
        return self


class ASRConfig(FrozenModel):
    backend: str = "faster_whisper_local"
    model: str = "large-v3"
    device: str = "auto"
    compute_type: str = "auto"
    use_batched: bool = True
    batch_size: int = 16
    vad: bool = True
    language: str = "zh"


class AudioExtractConfig(FrozenModel):
    sample_rate: int = 16000
    channels: int = 1

    @field_validator("sample_rate")
    @classmethod
    def validate_sample_rate(cls, value: int) -> int:
        if value not in {8000, 16000, 22050, 32000, 44100, 48000}:
            raise ValueError("unsupported sample_rate")
        return value

    @field_validator("channels")
    @classmethod
    def validate_channels(cls, value: int) -> int:
        if value not in {1, 2}:
            raise ValueError("channels must be 1 or 2")
        return value


class AudioSegmentConfig(FrozenModel):
    pass


class AudioRefineConfig(FrozenModel):
    mode: str = "adaptive"
    batch_size: int = 8

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in {"adaptive", "single_call", "batched", "serial"}:
            raise ValueError("refine mode must be one of: adaptive, single_call, batched, serial")
        return value

    @field_validator("batch_size")
    @classmethod
    def validate_batch_size(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("batch_size must be positive")
        return value


class AudioPipelineConfig(FrozenModel):
    extract: AudioExtractConfig = Field(default_factory=AudioExtractConfig)
    segment: AudioSegmentConfig = Field(default_factory=AudioSegmentConfig)
    refine: AudioRefineConfig = Field(default_factory=AudioRefineConfig)


class VisualFilterConfig(FrozenModel):
    detector: str = "content"
    threshold: float | str = "auto"
    auto_threshold_candidates: tuple[float, ...] = (10.0, 15.0, 20.0, 25.0, 27.0, 30.0, 35.0, 40.0)
    target_frames_per_minute: float = 1.5
    min_scene_len_seconds: float = 1.0
    representative: str = "content"
    candidate_fps: float = 3.0
    min_content_score: float = 0.5
    duplicate_pixel_mean_threshold: float = 0.025

    @field_validator("detector")
    @classmethod
    def validate_detector(cls, value: str) -> str:
        if value != "content":
            raise ValueError("visual filter detector must be: content")
        return value

    @field_validator("threshold", mode="before")
    @classmethod
    def validate_threshold(cls, value: object) -> object:
        if value == "auto":
            return value
        if not isinstance(value, int | float) or not math.isfinite(float(value)) or value <= 0:
            raise ValueError("visual filter threshold must be positive or auto")
        return float(value)

    @field_validator("auto_threshold_candidates")
    @classmethod
    def validate_auto_threshold_candidates(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if not value:
            raise ValueError("auto_threshold_candidates must not be empty")
        if any(not math.isfinite(item) or item <= 0 for item in value):
            raise ValueError("auto_threshold_candidates must be finite positive values")
        return value

    @field_validator("target_frames_per_minute", "min_scene_len_seconds", "candidate_fps", "min_content_score", "duplicate_pixel_mean_threshold")
    @classmethod
    def validate_positive_finite_float(cls, value: float, info) -> float:
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{info.field_name} must be finite and positive")
        return value

    @field_validator("representative")
    @classmethod
    def validate_representative(cls, value: str) -> str:
        if value not in {"content", "last", "middle"}:
            raise ValueError("representative must be one of: content, last, middle")
        return value


class VisualDescribeConfig(FrozenModel):
    concurrent_calls: int = 5

    @field_validator("concurrent_calls")
    @classmethod
    def validate_concurrent_calls(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("concurrent_calls must be positive")
        return value


class VisualPipelineConfig(FrozenModel):
    filter: VisualFilterConfig = Field(default_factory=VisualFilterConfig)
    describe: VisualDescribeConfig = Field(default_factory=VisualDescribeConfig)


class MergeOutlineConfig(FrozenModel):
    target_chapter_count_hint: str = "5-12"


class MergeSectionConfig(FrozenModel):
    concurrent_calls: int = 5

    @field_validator("concurrent_calls")
    @classmethod
    def validate_concurrent_calls(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("concurrent_calls must be positive")
        return value


class MergeAssembleConfig(FrozenModel):
    timestamp_format: str = "[{hms}]"
    include_toc: bool = True
    include_metadata: bool = True
    video_url_template: str | None = None
    top_title: str | None = None

    @model_validator(mode="after")
    def validate_templates(self) -> "MergeAssembleConfig":
        _validate_format_fields(self.timestamp_format, {"hms", "mmss", "seconds", "seconds_int"})
        if self.video_url_template is not None:
            _validate_format_fields(
                self.video_url_template,
                {"seconds", "seconds_int", "source_path", "source_filename", "hms"},
            )
        return self


class MergeConfig(FrozenModel):
    outline: MergeOutlineConfig = Field(default_factory=MergeOutlineConfig)
    section: MergeSectionConfig = Field(default_factory=MergeSectionConfig)
    assemble: MergeAssembleConfig = Field(default_factory=MergeAssembleConfig)


class AppConfig(FrozenModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    llm: LLMConfig
    tasks: dict[str, str]
    asr: ASRConfig = Field(default_factory=ASRConfig)
    audio_pipeline: AudioPipelineConfig = Field(default_factory=AudioPipelineConfig)
    visual_pipeline: VisualPipelineConfig = Field(default_factory=VisualPipelineConfig)
    merge: MergeConfig = Field(default_factory=MergeConfig)

    @model_validator(mode="after")
    def validate_tasks(self) -> "AppConfig":
        unknown = set(self.tasks) - LLM_TASK_NAMES
        if unknown:
            raise ValueError(f"unknown LLM task names: {sorted(unknown)}")
        missing = LLM_TASK_NAMES - set(self.tasks)
        if missing:
            raise ValueError(f"missing LLM task mappings: {sorted(missing)}")
        missing_profiles = {profile for profile in self.tasks.values() if profile not in self.llm.profiles}
        if missing_profiles:
            raise ValueError(f"tasks reference unknown profiles: {sorted(missing_profiles)}")
        return self


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or Path("config.yaml")
    if not config_path.exists():
        raise ConfigError(f"config file not found: {config_path}")
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return AppConfig.model_validate(payload)
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"failed to load config {config_path}: {exc}") from exc


def _validate_format_fields(template: str, allowed: set[str]) -> None:
    fields = {field_name for _, field_name, _, _ in string.Formatter().parse(template) if field_name}
    unknown = fields - allowed
    if unknown:
        raise ValueError(f"unsupported template fields: {sorted(unknown)}")
