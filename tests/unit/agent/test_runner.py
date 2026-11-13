"""Deterministic end-to-end checks for the bounded agent loop."""

import asyncio

import pytest

from open_code_agent.agent import (
    AgentLimits,
    AgentRunner,
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
    collect_agent_run,
)
from open_code_agent.agent.events import RunCompleted
from open_code_agent.protocol import (
    FinishReason,
    ModelError,
    ModelErrorCategory,
    StreamCompleted,
    StreamError,
    StreamStarted,
    TextDelta,
    ToolCall,
    ToolCallArgumentsDelta,
    ToolCallCompleted,
    ToolCallStarted,
    ToolResultContent,
    UsageInfo,
    UsageReported,
)
from open_code_agent.providers import MockModelProvider
from open_code_agent.tools import EchoTool, ScriptedTool, ToolRegistry


def registry(*tools: object) -> ToolRegistry:
    """Build an explicit tool registry for one test."""
    result = ToolRegistry()
    for tool in tools:
        result.register(tool)
    return result


async def test_text_only_run_forwards_events_and_returns_detached_result() -> None:
    provider = MockModelProvider(
        events=[
            StreamStarted(),
            TextDelta(text="hello"),
            UsageReported(
                usage=UsageInfo(input_tokens=2, output_tokens=1, total_tokens=3)
            ),
            StreamCompleted(finish_reason=FinishReason.STOP),
        ]
    )
    runner = AgentRunner(provider)
    request = AgentRunRequest(model="mock", task="hi")
    events = [event async for event in runner.stream(request)]
    assert isinstance(events[-1], RunCompleted)
    result: AgentRunResult = events[-1].result

    assert [event.type for event in events] == [
        "run_started",
        "model_step_started",
        "model_event_received",
        "model_event_received",
        "model_event_received",
        "model_event_received",
        "model_step_completed",
        "run_completed",
    ]
    assert result.status is AgentRunStatus.COMPLETED
    assert result.final_text == "hello"
    result.messages[0].metadata["changed"] = True
    assert (await collect_agent_run(runner, request)).messages[0].metadata == {}


async def test_one_tool_round_builds_correct_second_conversation_and_usage() -> None:
    call = ToolCall(id="call-1", name="echo", arguments={"message": "pong"})
    provider = MockModelProvider(
        event_sequences=[
            [
                StreamStarted(),
                ToolCallStarted(tool_call_id="call-1", name="echo"),
                ToolCallArgumentsDelta(
                    tool_call_id="call-1", arguments_delta='{"message": "pong"}'
                ),
                ToolCallCompleted(tool_call=call),
                UsageReported(usage=UsageInfo(input_tokens=1, total_tokens=1)),
                StreamCompleted(finish_reason=FinishReason.TOOL_CALLS),
            ],
            [
                StreamStarted(),
                TextDelta(text="done"),
                UsageReported(usage=UsageInfo(output_tokens=2, total_tokens=2)),
                StreamCompleted(finish_reason=FinishReason.STOP),
            ],
        ]
    )
    result = await collect_agent_run(
        AgentRunner(provider, registry(EchoTool())),
        AgentRunRequest(model="mock", task="use echo"),
    )

    assert result.status is AgentRunStatus.COMPLETED
    assert result.total_tool_calls == 1
    assert result.usage == UsageInfo(input_tokens=1, output_tokens=2, total_tokens=3)
    assert [
        message.role.value for message in provider.received_requests[1].messages
    ] == [
        "user",
        "assistant",
        "tool",
    ]


async def test_unknown_invalid_and_raised_tools_become_safe_tool_results() -> None:
    calls = [
        ToolCall(id="unknown", name="nope", arguments={}),
        ToolCall(id="bad", name="echo", arguments={}),
        ToolCall(id="raised", name="broken", arguments={}),
    ]
    provider = MockModelProvider(
        event_sequences=[
            [
                StreamStarted(),
                *[
                    item
                    for call in calls
                    for item in (
                        ToolCallStarted(tool_call_id=call.id, name=call.name),
                        ToolCallCompleted(tool_call=call),
                    )
                ],
                StreamCompleted(finish_reason=FinishReason.TOOL_CALLS),
            ],
            [
                StreamStarted(),
                TextDelta(text="recovered"),
                StreamCompleted(finish_reason=FinishReason.STOP),
            ],
        ]
    )
    result = await collect_agent_run(
        AgentRunner(
            provider,
            registry(
                EchoTool(),
                ScriptedTool(name="broken", responses=(RuntimeError("secret"),)),
            ),
        ),
        AgentRunRequest(model="mock", task="test"),
    )
    tool_messages = [
        message for message in result.messages if message.role.value == "tool"
    ]

    assert len(tool_messages) == 3
    assert all(
        part.is_error
        for message in tool_messages
        for part in message.content
        if isinstance(part, ToolResultContent)
    )
    assert "secret" not in str(tool_messages)


async def test_model_error_limit_and_cancellation_are_terminally_safe() -> None:
    provider = MockModelProvider(
        events=[
            StreamStarted(),
            StreamError(
                error=ModelError(category=ModelErrorCategory.RATE_LIMIT, message="slow")
            ),
        ]
    )
    failed = await collect_agent_run(
        AgentRunner(provider), AgentRunRequest(model="mock", task="x")
    )
    assert failed.status is AgentRunStatus.FAILED

    looping = MockModelProvider(
        events=[
            StreamStarted(),
            TextDelta(text="again"),
            StreamCompleted(finish_reason=FinishReason.STOP),
        ]
    )
    # A normal final response finishes before a step bound can be exceeded.
    assert (
        await collect_agent_run(
            AgentRunner(looping),
            AgentRunRequest(
                model="mock", task="x", limits=AgentLimits(max_model_steps=1)
            ),
        )
    ).status is AgentRunStatus.COMPLETED

    delayed = MockModelProvider(
        events=[StreamStarted(), TextDelta(text="late")], event_delay_seconds=1
    )
    task = asyncio.create_task(
        collect_agent_run(AgentRunner(delayed), AgentRunRequest(model="mock", task="x"))
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
