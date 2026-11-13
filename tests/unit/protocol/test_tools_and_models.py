"""Tests for tool, model, request, and usage protocol models."""

import pytest
from pydantic import ValidationError

from open_code_agent.protocol import (
    AgentMessage,
    JSONObject,
    JSONValue,
    MessageRole,
    ModelCapabilities,
    ModelRequest,
    TextContent,
    ToolCall,
    ToolDefinition,
    ToolResult,
    UsageInfo,
)


def user_message() -> AgentMessage:
    """Build a minimal valid message for model-request tests."""
    return AgentMessage(role=MessageRole.USER, content=[TextContent(text="Hello")])


def object_schema() -> JSONObject:
    """Build a minimal JSON Schema object."""
    return {"type": "object", "properties": {"path": {"type": "string"}}}


def test_tool_name_and_schema_validation() -> None:
    tool = ToolDefinition(
        name="repo.read-file",
        description="Read a repository file.",
        input_schema=object_schema(),
    )

    assert tool.name == "repo.read-file"
    with pytest.raises(ValidationError):
        ToolDefinition(
            name="read file", description="Read a file.", input_schema=object_schema()
        )
    with pytest.raises(ValidationError, match="type 'object'"):
        ToolDefinition(
            name="read_file",
            description="Read a file.",
            input_schema={"type": "string"},
        )


def test_nested_json_arguments_and_results_round_trip() -> None:
    arguments: JSONObject = {
        "paths": ["README.md", {"recursive": False}],
        "filters": {"extensions": [".py", ".toml"], "limit": None},
    }
    call = ToolCall(id="call-1", name="repo.search", arguments=arguments)
    result_content: JSONValue = [{"path": "src/main.py", "line": 1}, None]
    result = ToolResult(
        tool_call_id="call-1",
        name="repo.search",
        content=result_content,
        metadata={"elapsed_ms": 12.5},
    )

    assert ToolCall.model_validate(call.model_dump(mode="json")) == call
    assert ToolResult.model_validate(result.model_dump(mode="json")) == result


def test_non_json_compatible_tool_values_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolCall.model_validate(
            {"id": "call-1", "name": "repo.search", "arguments": {"value": object()}}
        )
    with pytest.raises(ValidationError):
        ToolResult.model_validate(
            {"tool_call_id": "call-1", "name": "repo.search", "content": object()}
        )


def test_duplicate_tool_names_are_rejected() -> None:
    duplicate_tools = [
        ToolDefinition(
            name="repo.read", description="Read a file.", input_schema=object_schema()
        ),
        ToolDefinition(
            name="repo.read",
            description="Read another file.",
            input_schema=object_schema(),
        ),
    ]

    with pytest.raises(ValidationError, match="unique"):
        ModelRequest(
            model="test-model", messages=[user_message()], tools=duplicate_tools
        )


@pytest.mark.parametrize("temperature", [-0.1, 2.1])
def test_model_request_temperature_validation(temperature: float) -> None:
    with pytest.raises(ValidationError):
        ModelRequest(
            model="test-model", messages=[user_message()], temperature=temperature
        )


@pytest.mark.parametrize("token_limit", [0, -1])
def test_model_request_output_token_validation(token_limit: int) -> None:
    with pytest.raises(ValidationError):
        ModelRequest(
            model="test-model", messages=[user_message()], max_output_tokens=token_limit
        )


@pytest.mark.parametrize("token_limit", [0, -1])
def test_capability_token_limit_validation(token_limit: int) -> None:
    with pytest.raises(ValidationError):
        ModelCapabilities(context_window=token_limit)
    with pytest.raises(ValidationError):
        ModelCapabilities(max_output_tokens=token_limit)


def test_usage_accepts_provider_reported_total_without_deriving_it() -> None:
    usage = UsageInfo(
        input_tokens=10,
        output_tokens=4,
        cached_input_tokens=8,
        reasoning_tokens=3,
        total_tokens=14,
    )

    assert usage.total_tokens == 14
    assert usage.model_dump(mode="json")["cached_input_tokens"] == 8


@pytest.mark.parametrize(
    "field_name",
    [
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
        "total_tokens",
    ],
)
def test_negative_usage_is_rejected(field_name: str) -> None:
    with pytest.raises(ValidationError):
        UsageInfo.model_validate({field_name: -1})
