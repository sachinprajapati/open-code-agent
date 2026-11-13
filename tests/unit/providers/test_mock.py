"""Tests for deterministic mock provider stream behavior."""

import asyncio
from collections.abc import AsyncIterator

import pytest

from open_code_agent.protocol import (
    AgentMessage,
    MessageRole,
    ModelError,
    ModelErrorCategory,
    ModelInfo,
    ModelRequest,
    ModelStreamEvent,
    StreamError,
    TextContent,
    TextDelta,
    ToolCall,
    ToolCallArgumentsDelta,
    ToolCallCompleted,
    UsageInfo,
)
from open_code_agent.protocol.models import ModelCapabilities
from open_code_agent.providers import MockModelProvider


def request() -> ModelRequest:
    """Build a small valid request for mock-stream tests."""
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


@pytest.mark.asyncio
async def test_text_scenario_preserves_event_order_and_records_requests() -> None:
    provider = MockModelProvider.text_response(
        ["Hello", " world"],
        model="mock-model",
        usage=UsageInfo(input_tokens=2, output_tokens=2, total_tokens=4),
    )
    model_request = request()

    events = await collect_events(provider.stream(model_request))

    assert [event.type for event in events] == [
        "stream_started",
        "text_delta",
        "text_delta",
        "usage_reported",
        "stream_completed",
    ]
    assert provider.invocation_count == 1
    assert provider.received_requests == (model_request,)


@pytest.mark.asyncio
async def test_tool_call_scenario_preserves_partial_argument_fragments() -> None:
    provider = MockModelProvider.tool_call_response(
        ToolCall(id="call-1", name="repo.read", arguments={"path": "README.md"}),
        ['{"path": "READ', 'ME.md"}'],
    )

    events = await collect_events(provider.stream(request()))
    fragments = [
        event.arguments_delta
        for event in events
        if isinstance(event, ToolCallArgumentsDelta)
    ]

    assert [event.type for event in events] == [
        "stream_started",
        "tool_call_started",
        "tool_call_arguments_delta",
        "tool_call_arguments_delta",
        "tool_call_completed",
        "stream_completed",
    ]
    assert fragments == ['{"path": "READ', 'ME.md"}']
    assert isinstance(events[-2], ToolCallCompleted)


@pytest.mark.asyncio
async def test_error_scenario_uses_normalized_stream_error() -> None:
    provider = MockModelProvider.error_response(
        ModelError(category=ModelErrorCategory.RATE_LIMIT, message="Slow down.")
    )

    events = await collect_events(provider.stream(request()))

    assert isinstance(events[-1], StreamError)
    assert events[-1].error.category is ModelErrorCategory.RATE_LIMIT


@pytest.mark.asyncio
async def test_repeated_streams_are_fresh_and_do_not_mutate_scripted_events() -> None:
    provider = MockModelProvider(events=[TextDelta(text="fixed")])

    first = await collect_events(provider.stream(request()))
    first[0].metadata["mutated"] = True
    second = await collect_events(provider.stream(request()))

    assert first[0] is not second[0]
    assert second[0].metadata == {}
    assert provider.invocation_count == 2


@pytest.mark.asyncio
async def test_request_history_and_model_listing_are_defensive_copies() -> None:
    info = ModelInfo(
        id="mock-model",
        display_name="Mock model",
        provider="mock",
        capabilities=ModelCapabilities(streaming=True),
    )
    provider = MockModelProvider(models=[info])
    original_request = request()
    await collect_events(provider.stream(original_request))
    original_request.model = "mutated-request"
    request_snapshot = provider.received_requests
    request_snapshot[0].model = "mutated-snapshot"
    model_snapshot = await provider.list_models()
    model_snapshot[0].display_name = "Mutated model"

    assert provider.received_requests[0].model == "mock-model"
    assert (await provider.list_models())[0].display_name == "Mock model"


@pytest.mark.asyncio
async def test_cancellation_propagates_without_becoming_stream_error() -> None:
    provider = MockModelProvider(
        events=[TextDelta(text="delayed")], event_delay_seconds=0.01
    )

    async def consume() -> None:
        await collect_events(provider.stream(request()))

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.invocation_count == 1
