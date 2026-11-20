"""Provider interface, registry, exceptions, and deterministic test provider."""

from open_code_agent.providers.base import (
    ConfigurationValidationResult,
    ModelProvider,
)
from open_code_agent.providers.exceptions import (
    DuplicateProviderError,
    InvalidProviderError,
    ProviderError,
    ProviderNotFoundError,
)
from open_code_agent.providers.mock import MockModelProvider
from open_code_agent.providers.openai_compatible import OpenAICompatibleProvider
from open_code_agent.providers.openai_responses import OpenAIResponsesProvider
from open_code_agent.providers.profiles import OpenAICompatibleProfile
from open_code_agent.providers.registry import ModelProviderRegistry
from open_code_agent.providers.secrets import (
    EnvironmentSecretResolver,
    MappingSecretResolver,
    SecretResolutionError,
    SecretResolver,
)

ManagedModelProvider = OpenAICompatibleProvider | OpenAIResponsesProvider

__all__ = [
    "ConfigurationValidationResult",
    "DuplicateProviderError",
    "EnvironmentSecretResolver",
    "InvalidProviderError",
    "MockModelProvider",
    "MappingSecretResolver",
    "ModelProvider",
    "ModelProviderRegistry",
    "OpenAICompatibleProfile",
    "OpenAICompatibleProvider",
    "OpenAIResponsesProvider",
    "ProviderError",
    "ProviderNotFoundError",
    "SecretResolver",
    "SecretResolutionError",
]


def provider_for_profile(profile: OpenAICompatibleProfile) -> ManagedModelProvider:
    """Construct the API adapter explicitly selected by a stored profile."""
    if profile.api == "responses":
        return OpenAIResponsesProvider(profile)
    return OpenAICompatibleProvider(profile)
