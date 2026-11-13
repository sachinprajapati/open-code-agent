"""Independent, deterministic tool registration and small schema validation."""

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import cast

from open_code_agent.protocol import JSONObject, ToolDefinition
from open_code_agent.tools.base import AgentTool, ToolRisk
from open_code_agent.tools.exceptions import (
    DuplicateToolError,
    InvalidToolError,
    ToolArgumentValidationError,
    ToolNotFoundError,
)

_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class ToolRegistry:
    """Store explicitly registered tools without a global singleton.

    Argument validation deliberately covers the small JSON Schema boundary used
    by this phase: object, required, properties, and additionalProperties,
    plus primitive property types used by the in-memory tools.  It is not a
    general JSON Schema implementation.
    """

    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: object) -> None:
        """Validate and add a tool without silently replacing another one."""
        validated = self._validate_tool(tool)
        if validated.name in self._tools:
            raise DuplicateToolError(f"Tool '{validated.name}' is already registered.")
        self._tools[validated.name] = validated

    def unregister(self, name: str) -> AgentTool:
        """Remove and return a registered tool by exact portable name."""
        self._validate_name(name)
        try:
            return self._tools.pop(name)
        except KeyError as error:
            raise ToolNotFoundError(f"Tool '{name}' is not registered.") from error

    def get(self, name: str) -> AgentTool:
        """Return a registered tool by exact portable name."""
        self._validate_name(name)
        try:
            return self._tools[name]
        except KeyError as error:
            raise ToolNotFoundError(f"Tool '{name}' is not registered.") from error

    def contains(self, name: str) -> bool:
        """Return whether an exact valid name has been registered."""
        self._validate_name(name)
        return name in self._tools

    def list_tools(self) -> tuple[AgentTool, ...]:
        """Return a deterministic immutable snapshot of registered objects."""
        return tuple(self._tools[name] for name in sorted(self._tools))

    def definitions(self) -> tuple[ToolDefinition, ...]:
        """Build model-facing definitions directly from the registered tools."""
        return tuple(
            ToolDefinition(
                name=tool.name,
                description=tool.description,
                input_schema=deepcopy(tool.input_schema),
            )
            for tool in self.list_tools()
        )

    def clear(self) -> None:
        """Remove all tools for a controlled application or test lifecycle."""
        self._tools.clear()

    @staticmethod
    def _validate_name(name: object) -> None:
        if not isinstance(name, str) or not _TOOL_NAME_PATTERN.fullmatch(name):
            raise InvalidToolError(
                "Tool name must use letters, numbers, '.', '_', or '-'."
            )

    @classmethod
    def _validate_tool(cls, tool: object) -> AgentTool:
        if not isinstance(tool, AgentTool):
            raise InvalidToolError("Object does not satisfy the AgentTool contract.")
        cls._validate_name(tool.name)
        if not isinstance(tool.description, str) or not tool.description.strip():
            raise InvalidToolError("Tool description must not be blank.")
        if not isinstance(tool.input_schema, dict):
            raise InvalidToolError("Tool input_schema must be an object.")
        try:
            ToolDefinition(
                name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
            )
        except ValueError as error:
            raise InvalidToolError(f"Invalid tool definition: {error}") from error
        if not isinstance(tool.risk, ToolRisk):
            raise InvalidToolError("Tool risk must be a ToolRisk value.")
        if not callable(tool.execute):
            raise InvalidToolError("Tool execute must be callable.")
        return tool


def validate_tool_arguments(schema: JSONObject, arguments: JSONObject) -> None:
    """Validate the intentionally small, documented schema subset.

    Registered schemas are trusted configuration, while ``arguments`` are
    model-generated and untrusted.  Unsupported schema keywords are ignored
    rather than being mistaken for a complete JSON Schema implementation.
    """
    if not isinstance(arguments, dict):
        raise ToolArgumentValidationError("Tool arguments must be an object.")
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(
        isinstance(item, str) for item in required
    ):
        raise ToolArgumentValidationError("Tool schema has an invalid required list.")
    for name in required:
        if name not in arguments:
            raise ToolArgumentValidationError(f"Missing required argument '{name}'.")
    properties_value = schema.get("properties", {})
    if not isinstance(properties_value, Mapping):
        raise ToolArgumentValidationError("Tool schema has invalid properties.")
    properties = cast(Mapping[str, object], properties_value)
    additional = schema.get("additionalProperties", True)
    if additional is False:
        unknown = set(arguments).difference(properties)
        if unknown:
            raise ToolArgumentValidationError(
                f"Unexpected argument '{sorted(unknown)[0]}'."
            )
    for name, value in arguments.items():
        property_schema = properties.get(name)
        if isinstance(property_schema, Mapping):
            _validate_value(name, value, property_schema)


def _validate_value(name: str, value: object, schema: Mapping[str, object]) -> None:
    expected = schema.get("type")
    valid = (
        expected is None
        or (expected == "string" and isinstance(value, str))
        or (
            expected == "number"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        )
        or (
            expected == "integer"
            and isinstance(value, int)
            and not isinstance(value, bool)
        )
        or (expected == "boolean" and isinstance(value, bool))
        or (expected == "object" and isinstance(value, dict))
        or (expected == "array" and isinstance(value, list))
    )
    if not valid:
        raise ToolArgumentValidationError(
            f"Argument '{name}' does not match expected type '{expected}'."
        )
