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
from open_code_agent.providers.profiles import OpenAICompatibleProfile
from open_code_agent.providers.registry import ModelProviderRegistry
from open_code_agent.providers.secrets import (
    EnvironmentSecretResolver,
    MappingSecretResolver,
    SecretResolutionError,
    SecretResolver,
)

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
    "ProviderError",
    "ProviderNotFoundError",
    "SecretResolver",
    "SecretResolutionError",
]
