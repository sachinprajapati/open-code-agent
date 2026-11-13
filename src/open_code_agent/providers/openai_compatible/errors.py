"""Local errors for OpenAI-compatible mapping and streaming infrastructure."""

from open_code_agent.protocol import ModelError
from open_code_agent.providers.exceptions import ProviderError


class OpenAICompatibleMappingError(ProviderError):
    """Raised before network I/O when protocol data cannot be represented safely."""


class OpenAICompatibleStreamError(ProviderError):
    """Raised internally for malformed compatible streaming payloads."""


class OpenAICompatibleRequestError(ProviderError):
    """Local wrapper retaining normalized data for a non-streaming request error."""

    def __init__(self, error: ModelError) -> None:
        """Store safe normalized error data without request secrets or headers."""
        super().__init__(error.message)
        self.model_error = error
