"""Reusable provider-contract assertions and MockModelProvider coverage."""

from collections.abc import AsyncIterator

import pytest

from open_code_agent.protocol import (
    AgentMessage,
    MessageRole,
    ModelRequest,
    ModelStreamEvent,
    TextContent,
)
from open_code_agent.providers import (
    ConfigurationValidationResult,
    MockModelProvider,
    ModelProvider,
)


def request() -> ModelRequest:
    """Build a small valid request for provider tests."""
    return ModelRequest(
        model="mock-model",
        messages=[
            AgentMessage(role=MessageRole.USER, content=[TextContent(text="Hello")])
        ],
    )


async def collect_events(
    stream: AsyncIterator[ModelStreamEvent],
) -> list[ModelStreamEvent]:
    """Collect events in source order without assembling a response."""
    return [event async for event in stream]


async def assert_provider_contract(provider: ModelProvider) -> None:
    """Reusable baseline contract for future offline provider implementations."""
    assert isinstance(provider, ModelProvider)
    assert provider.provider_id
    assert provider.display_name
    assert isinstance(await provider.list_models(), tuple)
    assert isinstance(
        await provider.validate_configuration(), ConfigurationValidationResult
    )
    assert isinstance(await collect_events(provider.stream(request())), list)


@pytest.mark.asyncio
async def test_mock_provider_satisfies_reusable_contract() -> None:
    provider = MockModelProvider.text_response(["Hello", " world"])

    await assert_provider_contract(provider)


@pytest.mark.asyncio
async def test_provider_identity_model_listing_and_validation_are_stable() -> None:
    provider = MockModelProvider(provider_id="local.mock", display_name="Local mock")

    assert provider.provider_id == "local.mock"
    assert provider.display_name == "Local mock"
    assert await provider.list_models() == ()
    assert await provider.validate_configuration() == ConfigurationValidationResult(
        valid=True
    )
