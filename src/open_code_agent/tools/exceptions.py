"""Expected local errors from the tool runtime."""

from open_code_agent.exceptions import OpenCodeAgentError


class ToolError(OpenCodeAgentError):
    """Base exception for local tool runtime errors."""


class ToolNotFoundError(ToolError):
    """Raised for an exact tool name that is not registered."""


class DuplicateToolError(ToolError):
    """Raised when a registry would replace an existing tool."""


class InvalidToolError(ToolError):
    """Raised when an object does not meet the registered-tool contract."""


class ToolArgumentValidationError(ToolError):
    """Raised before untrusted model arguments reach a tool implementation."""
