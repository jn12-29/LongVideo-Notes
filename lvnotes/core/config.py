from pathlib import Path
import re
import string

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lvnotes.core.constants import SUPPORTED_LLM_CAPABILITIES, SUPPORTED_LLM_PROVIDERS
from lvnotes.core.exceptions import ConfigError

LLM_TASK_NAMES = frozenset({"segment", "refine", "outline", "section", "slide_judge", "slide_describe"})
_VARIANT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


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


class VisualSampleConfig(FrozenModel):
    fps: float = 1


class VisualCropConfig(FrozenModel):
    left: float
    top: float
    right: float
    bottom: float

    @model_validator(mode="after")
    def validate_region(self) -> "VisualCropConfig":
        if not (0 <= self.left < self.right <= 1 and 0 <= self.top < self.bottom <= 1):
            raise ValueError("crop must satisfy 0 <= left < right <= 1 and 0 <= top < bottom <= 1")
        return self


class VisualFilterVariantConfig(FrozenModel):
    name: str
    phash_threshold: int = 8
    histogram_threshold: float = 0.12
    duplicate_phash_threshold: int = 2
    duplicate_histogram_threshold: float = 0.03
    duplicate_pixel_threshold: float = 0.02
    max_static_seconds: float | None = None
    crop: VisualCropConfig | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _VARIANT_NAME_RE.fullmatch(value):
            raise ValueError("filter variant name must contain only letters, digits, underscores, or hyphens")
        return value

    @field_validator("phash_threshold", "duplicate_phash_threshold")
    @classmethod
    def validate_phash_threshold(cls, value: int) -> int:
        if value < 0:
            raise ValueError("phash thresholds must be non-negative")
        return value

    @field_validator("histogram_threshold", "duplicate_histogram_threshold", "duplicate_pixel_threshold")
    @classmethod
    def validate_histogram_threshold(cls, value: float) -> float:
        if value < 0:
            raise ValueError("visual filter thresholds must be non-negative")
        return value

    @field_validator("max_static_seconds")
    @classmethod
    def validate_max_static_seconds(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("max_static_seconds must be positive")
        return value


class VisualFilterVariantFileConfig(FrozenModel):
    variants: list[VisualFilterVariantConfig]

    @model_validator(mode="after")
    def validate_variants(self) -> "VisualFilterVariantFileConfig":
        names = [variant.name for variant in self.variants]
        if len(names) != len(set(names)):
            raise ValueError("filter variant names must be unique")
        if not names:
            raise ValueError("filter variants file must define at least one variant")
        return self


class VisualFilterConfig(VisualFilterVariantConfig):
    name: str = "default"
    active_variant: str = "default"
    variants_file: Path | None = None

    @field_validator("active_variant")
    @classmethod
    def validate_active_variant(cls, value: str) -> str:
        if not _VARIANT_NAME_RE.fullmatch(value):
            raise ValueError("active_variant must contain only letters, digits, underscores, or hyphens")
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
    sample: VisualSampleConfig = Field(default_factory=VisualSampleConfig)
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
    filter_variants: VisualFilterVariantFileConfig | None = None

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
        if self.filter_variants is not None:
            variant_names = {variant.name for variant in self.filter_variants.variants}
            if self.visual_pipeline.filter.active_variant not in variant_names:
                raise ValueError(f"active_variant not found in filter variants: {self.visual_pipeline.filter.active_variant}")
        return self


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or Path("config.yaml")
    if not config_path.exists():
        raise ConfigError(f"config file not found: {config_path}")
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        _load_filter_variants(payload, config_path.parent)
        return AppConfig.model_validate(payload)
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"failed to load config {config_path}: {exc}") from exc


def _load_filter_variants(payload: object, config_dir: Path) -> None:
    if not isinstance(payload, dict):
        return
    visual_pipeline = payload.get("visual_pipeline")
    if not isinstance(visual_pipeline, dict):
        return
    filter_config = visual_pipeline.get("filter")
    if not isinstance(filter_config, dict):
        return
    variants_file = filter_config.get("variants_file")
    if variants_file in (None, ""):
        return
    variants_path = Path(variants_file)
    if not variants_path.is_absolute():
        variants_path = config_dir / variants_path
    if not variants_path.exists():
        raise ConfigError(f"filter variants file not found: {variants_path}")
    payload["filter_variants"] = yaml.safe_load(variants_path.read_text(encoding="utf-8")) or {}
    filter_config["variants_file"] = variants_path


def _validate_format_fields(template: str, allowed: set[str]) -> None:
    fields = {field_name for _, field_name, _, _ in string.Formatter().parse(template) if field_name}
    unknown = fields - allowed
    if unknown:
        raise ValueError(f"unsupported template fields: {sorted(unknown)}")
