"""Local Python exceptions for provider infrastructure."""

from open_code_agent.exceptions import OpenCodeAgentError


class ProviderError(OpenCodeAgentError):
    """Base exception for local provider infrastructure failures."""


class ProviderNotFoundError(ProviderError):
    """Raised when a requested provider ID is not registered."""


class DuplicateProviderError(ProviderError):
    """Raised when registering a provider ID that is already in use."""


class InvalidProviderError(ProviderError):
    """Raised when an object or provider ID violates the provider contract."""
