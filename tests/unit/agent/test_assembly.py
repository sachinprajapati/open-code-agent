"""Strict normalized model-stream assembly tests."""

import pytest

from open_code_agent.agent import AgentStepResult, AssistantResponseAssembler
from open_code_agent.agent.exceptions import AgentAssemblyError
from open_code_agent.protocol import (
    FinishReason,
    ModelError,
    ModelErrorCategory,
    ModelStreamEvent,
    ReasoningDelta,
    StreamCompleted,
    StreamError,
    StreamStarted,
    TextDelta,
    ToolCall,
    ToolCallArgumentsDelta,
    ToolCallCompleted,
    ToolCallStarted,
    UsageInfo,
    UsageReported,
)


def assemble(*events: ModelStreamEvent) -> AgentStepResult:
    """Consume typed event fixtures and return their complete step result."""
    assembler = AssistantResponseAssembler()
    for event in events:
        assembler.consume(event)
    return assembler.result()


def test_text_reasoning_usage_and_tool_order_are_assembled() -> None:
    call = ToolCall(id="call", name="echo", arguments={"message": "ok"})
    result = assemble(
        StreamStarted(),
        TextDelta(text="before "),
        ReasoningDelta(text="reason"),
        ToolCallStarted(tool_call_id="call", name="echo"),
        ToolCallArgumentsDelta(tool_call_id="call", arguments_delta='{"message":'),
        ToolCallArgumentsDelta(tool_call_id="call", arguments_delta=' "ok"}'),
        ToolCallCompleted(tool_call=call),
        TextDelta(text="after"),
        UsageReported(usage=UsageInfo(input_tokens=2, total_tokens=2)),
        StreamCompleted(finish_reason=FinishReason.TOOL_CALLS),
    )

    assert result.text == "before after"
    assert result.reasoning == "reason"
    assert result.tool_calls == [call]
    assert result.usage == UsageInfo(input_tokens=2, total_tokens=2)
    assert result.assistant_message is not None
    assert [part.type for part in result.assistant_message.content] == [
        "text",
        "tool_call",
        "text",
    ]


@pytest.mark.parametrize(
    "events",
    [
        [StreamStarted(), StreamStarted()],
        [
            StreamStarted(),
            StreamCompleted(finish_reason=FinishReason.STOP),
            TextDelta(text="late"),
        ],
        [
            StreamStarted(),
            ToolCallArgumentsDelta(tool_call_id="unknown", arguments_delta="{}"),
        ],
        [
            StreamStarted(),
            ToolCallCompleted(tool_call=ToolCall(id="x", name="echo", arguments={})),
        ],
        [
            StreamStarted(),
            ToolCallStarted(tool_call_id="x", name="echo"),
            StreamCompleted(finish_reason=FinishReason.TOOL_CALLS),
        ],
    ],
)
def test_invalid_stream_sequences_are_rejected(
    events: list[ModelStreamEvent],
) -> None:
    assembler = AssistantResponseAssembler()
    with pytest.raises(AgentAssemblyError):
        for event in events:
            assembler.consume(event)


def test_stream_error_is_a_failed_step_without_assistant_message() -> None:
    result = assemble(
        StreamStarted(),
        StreamError(
            error=ModelError(category=ModelErrorCategory.RATE_LIMIT, message="slow")
        ),
    )
    assert result.error is not None
    assert result.assistant_message is None
