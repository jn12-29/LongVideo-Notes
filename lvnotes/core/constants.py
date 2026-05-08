AUDIO_ONLY_MODE = "audio_only"
MULTIMODAL_MODE = "multimodal"

SUPPORTED_LLM_PROVIDERS = frozenset(
    {"openai_chat", "openai_responses", "anthropic_messages", "openai_compatible_chat"}
)
SUPPORTED_LLM_CAPABILITIES = frozenset(
    {"vision", "prompt_cache", "json_mode", "streaming", "reasoning"}
)
