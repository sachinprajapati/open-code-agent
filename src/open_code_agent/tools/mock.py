"""Deterministic in-memory development and testing tools only."""

import asyncio
from copy import deepcopy
from typing import Any

from open_code_agent.protocol import JSONObject
from open_code_agent.tools.base import (
    ToolExecutionContext,
    ToolExecutionResult,
    ToolRisk,
)


class EchoTool:
    """Development/testing utility that returns one supplied string."""

    name = "echo"
    description = "Return the supplied message unchanged."
    input_schema: JSONObject = {
        "type": "object",
        "required": ["message"],
        "properties": {"message": {"type": "string"}},
        "additionalProperties": False,
    }
    risk = ToolRisk.READ

    async def execute(
        self, arguments: JSONObject, context: ToolExecutionContext
    ) -> ToolExecutionResult:
        del context
        return ToolExecutionResult(content=arguments["message"])


class AddNumbersTool:
    """Development/testing utility that adds two JSON numeric values."""

    name = "add_numbers"
    description = "Add two numbers and return their sum."
    input_schema: JSONObject = {
        "type": "object",
        "required": ["a", "b"],
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "additionalProperties": False,
    }
    risk = ToolRisk.READ

    async def execute(
        self, arguments: JSONObject, context: ToolExecutionContext
    ) -> ToolExecutionResult:
        del context
        return ToolExecutionResult(content={"sum": arguments["a"] + arguments["b"]})  # type: ignore[operator]


class ScriptedTool:
    """Configurable in-memory tool for deterministic tests and examples.

    ``responses`` can contain a normalized result, JSON-compatible content, or
    an exception to raise.  Invocation history is always returned as deep-copy
    snapshots; optional delay/event waits make cancellation tests deterministic.
    """

    def __init__(
        self,
        *,
        name: str = "scripted",
        description: str = "Return configured in-memory scripted responses.",
        input_schema: JSONObject | None = None,
        responses: tuple[ToolExecutionResult | Any | Exception, ...] = (),
        risk: ToolRisk = ToolRisk.READ,
        delay_seconds: float = 0.0,
        wait_event: asyncio.Event | None = None,
    ) -> None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds must be non-negative.")
        self.name = name
        self.description = description
        self.input_schema = input_schema or {"type": "object"}
        self.risk = risk
        self._responses = responses or (ToolExecutionResult(content=None),)
        self._delay_seconds = delay_seconds
        self._wait_event = wait_event
        self._invocations: list[JSONObject] = []

    @property
    def invocations(self) -> tuple[JSONObject, ...]:
        """Return recorded arguments without exposing mutable internal history."""
        return tuple(deepcopy(item) for item in self._invocations)

    async def execute(
        self, arguments: JSONObject, context: ToolExecutionContext
    ) -> ToolExecutionResult:
        del context
        self._invocations.append(deepcopy(arguments))
        if self._wait_event is not None:
            await self._wait_event.wait()
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        response = self._responses[
            min(len(self._invocations) - 1, len(self._responses) - 1)
        ]
        if isinstance(response, Exception):
            raise response
        if isinstance(response, ToolExecutionResult):
            return response.model_copy(deep=True)
        return ToolExecutionResult(content=deepcopy(response))
