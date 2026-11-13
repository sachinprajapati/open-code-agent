"""Normalized streaming events emitted by future provider adapters."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from open_code_agent.protocol.common import JSONObject, ProtocolModel
from open_code_agent.protocol.errors import ModelError
from open_code_agent.protocol.models import UsageInfo
from open_code_agent.protocol.tools import ToolCall


class FinishReason(StrEnum):
    """Portable reasons a model stream ended."""

    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class StreamEvent(ProtocolModel):
    """Common fields for all normalized streaming events."""

    metadata: JSONObject = Field(default_factory=dict)


class StreamStarted(StreamEvent):
    """Marks the beginning of a streamed model response."""

    type: Literal["stream_started"] = "stream_started"
    provider_request_id: str | None = None
    model: str | None = None


class TextDelta(StreamEvent):
    """A non-empty incremental text response fragment."""

    type: Literal["text_delta"] = "text_delta"
    text: str = Field(min_length=1)


class ReasoningDelta(StreamEvent):
    """A non-empty incremental reasoning response fragment."""

    type: Literal["reasoning_delta"] = "reasoning_delta"
    text: str = Field(min_length=1)


class ToolCallStarted(StreamEvent):
    """Signals that a streamed tool invocation has begun."""

    type: Literal["tool_call_started"] = "tool_call_started"
    tool_call_id: str = Field(min_length=1)
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    index: int | None = Field(default=None, ge=0)


class ToolCallArgumentsDelta(StreamEvent):
    """A raw, potentially incomplete JSON argument fragment."""

    type: Literal["tool_call_arguments_delta"] = "tool_call_arguments_delta"
    tool_call_id: str = Field(min_length=1)
    arguments_delta: str
    index: int | None = Field(default=None, ge=0)


class ToolCallCompleted(StreamEvent):
    """Signals a complete, validated streamed tool invocation."""

    type: Literal["tool_call_completed"] = "tool_call_completed"
    tool_call: ToolCall


class UsageReported(StreamEvent):
    """Reports provider token-accounting information."""

    type: Literal["usage_reported"] = "usage_reported"
    usage: UsageInfo


class StreamCompleted(StreamEvent):
    """Marks successful or expected completion of a model stream."""

    type: Literal["stream_completed"] = "stream_completed"
    finish_reason: FinishReason = Field(strict=False)
    provider_finish_reason: str | None = None


class StreamError(StreamEvent):
    """Signals a normalized error while processing a model stream."""

    type: Literal["stream_error"] = "stream_error"
    error: ModelError


type ModelStreamEvent = Annotated[
    StreamStarted
    | TextDelta
    | ReasoningDelta
    | ToolCallStarted
    | ToolCallArgumentsDelta
    | ToolCallCompleted
    | UsageReported
    | StreamCompleted
    | StreamError,
    Field(discriminator="type"),
]
