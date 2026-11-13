"""Deterministic in-memory provider for tests and local development."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Self

from open_code_agent.protocol import (
    FinishReason,
    ModelError,
    ModelInfo,
    ModelRequest,
    ModelStreamEvent,
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
from open_code_agent.providers.base import ConfigurationValidationResult


class MockModelProvider:
    """A scripted provider that creates fresh streams without network access.

    Sequential use is the primary testing target. Concurrent streams are
    independent, but the ordering of request-history entries across tasks is
    naturally determined by event-loop scheduling.
    """

    def __init__(
        self,
        *,
        provider_id: str = "mock",
        display_name: str = "Mock provider",
        events: Sequence[ModelStreamEvent] = (),
        event_sequences: Sequence[Sequence[ModelStreamEvent]] | None = None,
        models: Sequence[ModelInfo] = (),
        validation_result: ConfigurationValidationResult | None = None,
        event_delay_seconds: float = 0.0,
        yield_control: bool = False,
    ) -> None:
        """Configure deterministic events, models, validation, and scheduling."""
        if not provider_id.strip():
            raise ValueError("provider_id must not be blank.")
        if not display_name.strip():
            raise ValueError("display_name must not be blank.")
        if event_delay_seconds < 0:
            raise ValueError("event_delay_seconds must be non-negative.")

        self._provider_id = provider_id
        self._display_name = display_name
        self._events = tuple(event.model_copy(deep=True) for event in events)
        self._event_sequences = (
            tuple(
                tuple(event.model_copy(deep=True) for event in sequence)
                for sequence in event_sequences
            )
            if event_sequences is not None
            else None
        )
        self._models = tuple(model.model_copy(deep=True) for model in models)
        self._validation_result = (
            validation_result or ConfigurationValidationResult(valid=True)
        ).model_copy(deep=True)
        self._event_delay_seconds = event_delay_seconds
        self._yield_control = yield_control
        self._received_requests: list[ModelRequest] = []
        self._invocation_count = 0

    @property
    def provider_id(self) -> str:
        """Return the configured stable provider ID."""
        return self._provider_id

    @property
    def display_name(self) -> str:
        """Return the configured human-readable provider name."""
        return self._display_name

    @property
    def invocation_count(self) -> int:
        """Return the number of streams that have begun consumption."""
        return self._invocation_count

    @property
    def received_requests(self) -> tuple[ModelRequest, ...]:
        """Return deep-copied request history without exposing internal state."""
        return tuple(
            request.model_copy(deep=True) for request in self._received_requests
        )

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        """Yield fresh deep copies of scripted events in configured order."""
        return self._stream(request)

    async def _stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        self._received_requests.append(request.model_copy(deep=True))
        invocation_index = self._invocation_count
        self._invocation_count += 1
        if self._event_sequences is None:
            events = self._events
        else:
            try:
                events = self._event_sequences[invocation_index]
            except IndexError as error:
                raise RuntimeError(
                    "Mock provider has no scripted stream for this call."
                ) from error
        for event in events:
            if self._event_delay_seconds:
                await asyncio.sleep(self._event_delay_seconds)
            elif self._yield_control:
                await asyncio.sleep(0)
            yield event.model_copy(deep=True)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        """Return deep-copied configured model records."""
        return tuple(model.model_copy(deep=True) for model in self._models)

    async def validate_configuration(self) -> ConfigurationValidationResult:
        """Return a deep copy of the configured validation result."""
        return self._validation_result.model_copy(deep=True)

    @classmethod
    def text_response(
        cls,
        text_deltas: Sequence[str],
        *,
        model: str | None = None,
        usage: UsageInfo | None = None,
        provider_id: str = "mock",
        display_name: str = "Mock provider",
    ) -> Self:
        """Create a mock that streams text then stops normally."""
        if not text_deltas:
            raise ValueError("text_deltas must contain at least one value.")
        events: list[ModelStreamEvent] = [StreamStarted(model=model)]
        events.extend(TextDelta(text=text) for text in text_deltas)
        if usage is not None:
            events.append(UsageReported(usage=usage))
        events.append(StreamCompleted(finish_reason=FinishReason.STOP))
        return cls(
            provider_id=provider_id,
            display_name=display_name,
            events=events,
        )

    @classmethod
    def tool_call_response(
        cls,
        tool_call: ToolCall,
        argument_deltas: Sequence[str],
        *,
        model: str | None = None,
        provider_id: str = "mock",
        display_name: str = "Mock provider",
    ) -> Self:
        """Create a mock that streams a complete normalized tool call."""
        if not argument_deltas:
            raise ValueError("argument_deltas must contain at least one value.")
        events: list[ModelStreamEvent] = [
            StreamStarted(model=model),
            ToolCallStarted(tool_call_id=tool_call.id, name=tool_call.name),
        ]
        events.extend(
            ToolCallArgumentsDelta(
                tool_call_id=tool_call.id,
                arguments_delta=fragment,
            )
            for fragment in argument_deltas
        )
        events.extend(
            [
                ToolCallCompleted(tool_call=tool_call),
                StreamCompleted(finish_reason=FinishReason.TOOL_CALLS),
            ]
        )
        return cls(
            provider_id=provider_id,
            display_name=display_name,
            events=events,
        )

    @classmethod
    def error_response(
        cls,
        error: ModelError,
        *,
        model: str | None = None,
        provider_id: str = "mock",
        display_name: str = "Mock provider",
    ) -> Self:
        """Create a mock that reports a normalized terminal stream error."""
        return cls(
            provider_id=provider_id,
            display_name=display_name,
            events=[StreamStarted(model=model), StreamError(error=error)],
        )
