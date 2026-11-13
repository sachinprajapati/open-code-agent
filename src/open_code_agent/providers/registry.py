"""Independent registry for provider implementations."""

import re

from open_code_agent.providers.base import ModelProvider
from open_code_agent.providers.exceptions import (
    DuplicateProviderError,
    InvalidProviderError,
    ProviderNotFoundError,
)

_PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


class ModelProviderRegistry:
    """Store provider implementations by preserved, lowercase provider ID.

    Each registry is independent. IDs are not normalized: callers register and
    retrieve the same validated lowercase ASCII ID, and listings are sorted by
    that ID for deterministic behavior.
    """

    def __init__(self) -> None:
        """Create an empty independent provider registry."""
        self._providers: dict[str, ModelProvider] = {}

    def register(self, provider: object) -> None:
        """Register a provider, rejecting invalid and duplicate identities."""
        validated_provider = self._validate_provider(provider)
        provider_id = validated_provider.provider_id
        if provider_id in self._providers:
            raise DuplicateProviderError(
                f"Provider '{provider_id}' is already registered."
            )
        self._providers[provider_id] = validated_provider

    def unregister(self, provider_id: str) -> ModelProvider:
        """Remove and return a registered provider by its exact ID."""
        self._validate_provider_id(provider_id)
        try:
            return self._providers.pop(provider_id)
        except KeyError as error:
            raise ProviderNotFoundError(
                f"Provider '{provider_id}' is not registered."
            ) from error

    def get(self, provider_id: str) -> ModelProvider:
        """Return a registered provider by its exact ID."""
        self._validate_provider_id(provider_id)
        try:
            return self._providers[provider_id]
        except KeyError as error:
            raise ProviderNotFoundError(
                f"Provider '{provider_id}' is not registered."
            ) from error

    def contains(self, provider_id: str) -> bool:
        """Return whether an exact, valid provider ID is registered."""
        self._validate_provider_id(provider_id)
        return provider_id in self._providers

    def list_providers(self) -> tuple[ModelProvider, ...]:
        """Return a deterministic, immutable snapshot of registered providers."""
        return tuple(
            self._providers[provider_id] for provider_id in sorted(self._providers)
        )

    def clear(self) -> None:
        """Remove all providers for controlled test or application lifecycle use."""
        self._providers.clear()

    @staticmethod
    def _validate_provider(provider: object) -> ModelProvider:
        """Check the runtime protocol and stable provider identity."""
        if not isinstance(provider, ModelProvider):
            raise InvalidProviderError(
                "Object does not satisfy the ModelProvider contract."
            )
        provider_id = provider.provider_id
        display_name = provider.display_name
        ModelProviderRegistry._validate_provider_id(provider_id)
        if not isinstance(display_name, str) or not display_name.strip():
            raise InvalidProviderError("Provider display_name must not be blank.")
        return provider

    @staticmethod
    def _validate_provider_id(provider_id: str) -> None:
        """Validate a portable, exact provider identifier."""
        if not isinstance(provider_id, str) or not _PROVIDER_ID_PATTERN.fullmatch(
            provider_id
        ):
            raise InvalidProviderError(
                "Provider ID must use lowercase letters, numbers, '.', '_', or '-'."
            )
