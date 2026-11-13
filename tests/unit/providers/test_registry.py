"""Tests for the independent provider registry."""

import pytest

from open_code_agent.providers import (
    DuplicateProviderError,
    InvalidProviderError,
    MockModelProvider,
    ModelProviderRegistry,
    ProviderNotFoundError,
)


def test_register_retrieve_and_deterministically_list_providers() -> None:
    registry = ModelProviderRegistry()
    zeta = MockModelProvider(provider_id="zeta")
    alpha = MockModelProvider(provider_id="alpha")

    registry.register(zeta)
    registry.register(alpha)

    assert registry.contains("alpha")
    assert registry.get("zeta") is zeta
    assert tuple(provider.provider_id for provider in registry.list_providers()) == (
        "alpha",
        "zeta",
    )


def test_registry_rejects_duplicates_invalid_ids_and_invalid_objects() -> None:
    registry = ModelProviderRegistry()
    registry.register(MockModelProvider(provider_id="mock"))

    with pytest.raises(DuplicateProviderError, match="already registered"):
        registry.register(MockModelProvider(provider_id="mock"))
    with pytest.raises(InvalidProviderError):
        registry.register(MockModelProvider(provider_id="Invalid Provider"))
    with pytest.raises(InvalidProviderError, match="contract"):
        registry.register(object())
    with pytest.raises(InvalidProviderError):
        registry.contains("invalid provider")


def test_unknown_lookup_unregister_and_registry_instances_are_independent() -> None:
    first = ModelProviderRegistry()
    second = ModelProviderRegistry()
    provider = MockModelProvider(provider_id="mock")
    first.register(provider)

    with pytest.raises(ProviderNotFoundError, match="not registered"):
        first.get("unknown")
    with pytest.raises(ProviderNotFoundError, match="not registered"):
        first.unregister("unknown")

    assert not second.contains("mock")
    assert first.unregister("mock") is provider
    assert not first.contains("mock")


def test_registry_listing_cannot_expose_mutable_internal_mapping() -> None:
    registry = ModelProviderRegistry()
    registry.register(MockModelProvider(provider_id="mock"))
    snapshot = registry.list_providers()

    with pytest.raises(AttributeError):
        snapshot.append(MockModelProvider(provider_id="other"))  # type: ignore[attr-defined]

    assert registry.contains("mock")
    registry.clear()
    assert registry.list_providers() == ()
