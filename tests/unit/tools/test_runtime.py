"""Tests for the intentionally small in-memory tool runtime."""

import asyncio
from typing import cast

import pytest

from open_code_agent.tools import (
    AddNumbersTool,
    DuplicateToolError,
    EchoTool,
    ScriptedTool,
    ToolArgumentValidationError,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolNotFoundError,
    ToolRegistry,
    validate_tool_arguments,
)


def context() -> ToolExecutionContext:
    """Build safe context for direct mock-tool tests."""
    return ToolExecutionContext(run_id="run", step=1, tool_call_id="call")


def test_registry_is_independent_sorted_and_rejects_duplicates() -> None:
    first = ToolRegistry()
    second = ToolRegistry()
    first.register(EchoTool())
    first.register(AddNumbersTool())

    assert [tool.name for tool in first.list_tools()] == ["add_numbers", "echo"]
    assert [definition.name for definition in first.definitions()] == [
        "add_numbers",
        "echo",
    ]
    assert not second.contains("echo")
    with pytest.raises(DuplicateToolError):
        first.register(EchoTool())
    with pytest.raises(ToolNotFoundError):
        second.get("echo")
    assert first.unregister("echo").name == "echo"


def test_small_schema_validation_rejects_bad_arguments() -> None:
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(EchoTool.input_schema, {})
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(EchoTool.input_schema, {"message": 3})
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(EchoTool.input_schema, {"message": "ok", "x": 1})


async def test_in_memory_tools_are_deterministic_and_history_is_isolated() -> None:
    echo = EchoTool()
    numbers = AddNumbersTool()
    scripted = ScriptedTool(responses=(ToolExecutionResult(content={"ok": True}),))

    assert (await echo.execute({"message": "hello"}, context())).content == "hello"
    assert (await numbers.execute({"a": 2, "b": 3.5}, context())).content == {
        "sum": 5.5
    }
    await scripted.execute({"value": [1]}, context())
    snapshot = scripted.invocations
    value = cast(list[object], snapshot[0]["value"])
    value.append(2)
    assert scripted.invocations == ({"value": [1]},)


async def test_scripted_tool_propagates_cancellation() -> None:
    gate = asyncio.Event()
    tool = ScriptedTool(wait_event=gate)
    task = asyncio.create_task(tool.execute({}, context()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
