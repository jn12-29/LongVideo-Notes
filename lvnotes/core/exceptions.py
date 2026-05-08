class LVNotesError(Exception):
    """Base class for expected project errors."""


class ConfigError(LVNotesError):
    """Configuration loading or validation failed."""


class CacheError(LVNotesError):
    """Required cached artifact is missing or invalid."""


class LLMError(LVNotesError):
    """LLM provider or output handling failed."""


class AuthError(LLMError):
    """LLM authentication failed."""


class RateLimitError(LLMError):
    """LLM endpoint rate limit or quota was reached."""


class ContextLengthError(LLMError):
    """LLM context budget was exceeded."""


class TransportError(LLMError):
    """External LLM transport failed."""


class ASRError(LVNotesError):
    """ASR backend failed."""


class MediaError(LVNotesError):
    """Media probing or conversion failed."""
