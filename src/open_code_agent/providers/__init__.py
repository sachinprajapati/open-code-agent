"""Provider interface, registry, exceptions, and deterministic test provider."""

from urllib.parse import urlsplit

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


def provider_for_profile(
    profile: OpenAICompatibleProfile, *, secret_resolver: SecretResolver | None = None
) -> ManagedModelProvider:
    """Construct the adapter selected by a profile or required by its model."""
    if profile.api == "responses" or _requires_openai_responses(profile):
        responses_profile = profile.model_copy(update={"api": "responses"})
        return OpenAIResponsesProvider(
            responses_profile, secret_resolver=secret_resolver
        )
    return OpenAICompatibleProvider(profile, secret_resolver=secret_resolver)


def _requires_openai_responses(profile: OpenAICompatibleProfile) -> bool:
    """Use Responses for official GPT-6 models with native agent tools.

    Old persisted profiles predate API selection and default to Chat
    Completions. GPT-6 rejects function tools on that endpoint, while the
    equivalent Responses endpoint supports them. Restrict the compatibility
    upgrade to OpenAI's canonical host so third-party compatible endpoints are
    never silently switched.
    """
    return (
        urlsplit(profile.base_url).hostname == "api.openai.com"
        and profile.default_model.lower().startswith("gpt-6")
    )
