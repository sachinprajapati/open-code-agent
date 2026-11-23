"""Strict assembly of one normalized provider stream into an assistant step."""

from dataclasses import dataclass

from open_code_agent.agent.exceptions import AgentAssemblyError
from open_code_agent.protocol import (
    AgentMessage,
    FinishReason,
    MessageContentPart,
    MessageRole,
    ModelError,
    ModelStreamEvent,
    ReasoningDelta,
    StreamCompleted,
    StreamError,
    StreamStarted,
    TextContent,
    TextDelta,
    ToolCall,
    ToolCallArgumentsDelta,
    ToolCallCompleted,
    ToolCallContent,
    ToolCallStarted,
    UsageInfo,
    UsageReported,
)
from open_code_agent.protocol.common import ProtocolModel


class AgentStepResult(ProtocolModel):
    """A validated result for exactly one model stream."""

    assistant_message: AgentMessage | None = None
    text: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = []
    usage: UsageInfo | None = None
    finish_reason: FinishReason | None = None
    error: ModelError | None = None


@dataclass
class _StartedCall:
    name: str
    fragments: list[str]


class AssistantResponseAssembler:
    """Consume and validate a single provider-neutral model stream.

    A completed call must have been started first.  Argument deltas are kept
    only for protocol validation and ordering; the provider's validated
    ``ToolCallCompleted`` arguments are the only values eligible for execution.
    """

    def __init__(self) -> None:
        self._started = False
        self._terminal = False
        self._error: ModelError | None = None
        self._finish_reason: FinishReason | None = None
        self._text: list[str] = []
        self._reasoning: list[str] = []
        self._calls: dict[str, _StartedCall] = {}
        self._completed: dict[str, ToolCall] = {}
        self._order: list[tuple[str, str]] = []
        self._usage: UsageInfo | None = None

    def consume(self, event: ModelStreamEvent) -> None:
        """Add one event, rejecting malformed ordering immediately."""
        if self._terminal:
            raise AgentAssemblyError(
                "Received a model event after stream terminal event."
            )
        # A provider can fail before it receives any response bytes (for
        # example, while resolving credentials or after an HTTP status error).
        # Such a normalized error is terminal and does not require the usual
        # stream_started preamble.
        if isinstance(event, StreamError):
            self._error = event.error.model_copy(deep=True)
            self._terminal = True
            return
        if isinstance(event, StreamStarted):
            if self._started:
                raise AgentAssemblyError("Received duplicate stream_started event.")
            self._started = True
            return
        if not self._started:
            raise AgentAssemblyError("Model stream must begin with stream_started.")
        if isinstance(event, TextDelta):
            self._text.append(event.text)
            self._order.append(("text", event.text))
        elif isinstance(event, ReasoningDelta):
            self._reasoning.append(event.text)
        elif isinstance(event, ToolCallStarted):
            if event.tool_call_id in self._calls:
                raise AgentAssemblyError("Received duplicate tool-call start ID.")
            self._calls[event.tool_call_id] = _StartedCall(event.name, [])
            self._order.append(("call", event.tool_call_id))
        elif isinstance(event, ToolCallArgumentsDelta):
            try:
                self._calls[event.tool_call_id].fragments.append(event.arguments_delta)
            except KeyError as error:
                raise AgentAssemblyError(
                    "Received argument delta for unknown tool call."
                ) from error
        elif isinstance(event, ToolCallCompleted):
            call = event.tool_call
            started = self._calls.get(call.id)
            if started is None:
                raise AgentAssemblyError(
                    "Received completed tool call without a start event."
                )
            if call.id in self._completed:
                raise AgentAssemblyError("Received duplicate completed tool-call ID.")
            if call.name != started.name:
                raise AgentAssemblyError(
                    "Tool-call completion name does not match start event."
                )
            self._completed[call.id] = call.model_copy(deep=True)
        elif isinstance(event, UsageReported):
            self._usage = event.usage.model_copy(deep=True)
        elif isinstance(event, StreamCompleted):
            incomplete = set(self._calls).difference(self._completed)
            if incomplete:
                raise AgentAssemblyError(
                    "Stream completed with an incomplete tool call."
                )
            self._finish_reason = event.finish_reason
            self._terminal = True

    def result(self) -> AgentStepResult:
        """Return a structured result after exactly one terminal event."""
        if not self._terminal:
            raise AgentAssemblyError("Model stream ended without a terminal event.")
        if self._error is not None:
            return AgentStepResult(error=self._error, usage=self._usage)
        if not self._started:
            raise AgentAssemblyError("Model stream did not include stream_started.")
        content: list[MessageContentPart] = []
        calls: list[ToolCall] = []
        for kind, value in self._order:
            if kind == "text":
                content.append(TextContent(text=value))
            else:
                call = self._completed[value]
                calls.append(call.model_copy(deep=True))
                content.append(ToolCallContent(**call.model_dump()))
        message = (
            AgentMessage(role=MessageRole.ASSISTANT, content=content)
            if content
            else None
        )
        return AgentStepResult(
            assistant_message=message,
            text="".join(self._text),
            reasoning="".join(self._reasoning),
            tool_calls=calls,
            usage=self._usage,
            finish_reason=self._finish_reason,
        )
