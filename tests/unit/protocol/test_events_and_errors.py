"""Tests for normalized streaming events and model errors."""

import pytest
from pydantic import TypeAdapter, ValidationError

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
from open_code_agent.protocol.events import StreamEvent


def test_every_stream_event_round_trips_through_discriminated_union() -> None:
    events: list[StreamEvent] = [
        StreamStarted(provider_request_id="request-1", model="test-model"),
        TextDelta(text="Hello"),
        ReasoningDelta(text="Considering the request."),
        ToolCallStarted(tool_call_id="call-1", name="repo.read", index=0),
        ToolCallArgumentsDelta(
            tool_call_id="call-1", arguments_delta='{"path": "src/', index=0
        ),
        ToolCallCompleted(
            tool_call=ToolCall(
                id="call-1", name="repo.read", arguments={"path": "src/app.py"}
            )
        ),
        UsageReported(usage=UsageInfo(input_tokens=3, output_tokens=2, total_tokens=5)),
        StreamCompleted(
            finish_reason=FinishReason.TOOL_CALLS, provider_finish_reason="tool_use"
        ),
        StreamError(
            error=ModelError(
                category=ModelErrorCategory.RATE_LIMIT,
                message="Too many requests.",
                retryable=True,
                retry_after_seconds=1.5,
            )
        ),
    ]
    adapter = TypeAdapter(ModelStreamEvent)

    for event in events:
        restored = adapter.validate_python(event.model_dump(mode="json"))

        assert type(restored) is type(event)
        assert adapter.dump_python(restored, mode="json") == event.model_dump(
            mode="json"
        )


def test_discriminated_event_parsing_and_partial_argument_preservation() -> None:
    adapter = TypeAdapter(ModelStreamEvent)
    fragment = '{"query": "incomplete'

    event = adapter.validate_python(
        {
            "type": "tool_call_arguments_delta",
            "tool_call_id": "call-2",
            "arguments_delta": fragment,
            "metadata": {"chunk": 2},
        }
    )

    assert isinstance(event, ToolCallArgumentsDelta)
    assert event.arguments_delta == fragment
    assert event.metadata == {"chunk": 2}


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "text_delta", "text": ""},
        {"type": "reasoning_delta", "text": ""},
        {"type": "unrecognized"},
    ],
)
def test_invalid_stream_events_are_rejected(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ModelStreamEvent).validate_python(payload)


def test_model_error_validation_and_round_trip() -> None:
    error = ModelError(
        category=ModelErrorCategory.CONNECTION,
        message="Connection reset.",
        retryable=True,
        provider="example",
        provider_error_code="ECONNRESET",
        http_status=503,
        retry_after_seconds=0,
        metadata={"attempt": 2},
    )

    restored = ModelError.model_validate(error.model_dump(mode="json"))

    assert restored == error
    with pytest.raises(ValidationError):
        ModelError.model_validate({"category": "timeout", "message": " "})
    with pytest.raises(ValidationError):
        ModelError.model_validate(
            {"category": "timeout", "message": "Timed out", "http_status": 99}
        )
    with pytest.raises(ValidationError):
        ModelError.model_validate(
            {
                "category": "timeout",
                "message": "Timed out",
                "retry_after_seconds": -1,
            }
        )
