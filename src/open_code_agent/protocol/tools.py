"""Provider-neutral tool declarations and tool-call data."""

from typing import Literal

from pydantic import Field, field_validator

from open_code_agent.protocol.common import JSONObject, JSONValue, ProtocolModel


class ToolDefinition(ProtocolModel):
    """A tool that a provider may expose to a model."""

    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    description: str = Field(min_length=1)
    input_schema: JSONObject

    @field_validator("description")
    @classmethod
    def description_must_not_be_blank(cls, value: str) -> str:
        """Reject descriptions that contain only whitespace."""
        if not value.strip():
            raise ValueError("Tool description must not be blank.")
        return value

    @field_validator("input_schema")
    @classmethod
    def input_schema_must_be_an_object_schema(cls, value: JSONObject) -> JSONObject:
        """Require the schema's top-level JSON type to be an object."""
        if value.get("type") != "object":
            raise ValueError("Tool input_schema must declare type 'object'.")
        return value


class ToolCall(ProtocolModel):
    """A complete normalized tool invocation requested by a model."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    arguments: JSONObject


class ToolResult(ProtocolModel):
    """A normalized result returned for a tool invocation."""

    tool_call_id: str = Field(min_length=1)
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    content: JSONValue
    is_error: bool = False
    metadata: JSONObject | None = None


class ToolCallContent(ToolCall):
    """A tool invocation embedded in an assistant message."""

    type: Literal["tool_call"] = "tool_call"


class ToolResultContent(ToolResult):
    """A tool result embedded in a tool-role message."""

    type: Literal["tool_result"] = "tool_result"
