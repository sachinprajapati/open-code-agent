"""Safe, provider-independent contracts for agent tools."""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import Field

from open_code_agent.protocol import JSONObject, JSONValue
from open_code_agent.protocol.common import ProtocolModel


class ToolRisk(StrEnum):
    """Conservative risk metadata for future approval policy."""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"


class ToolExecutionContext(ProtocolModel):
    """The intentionally small context provided to a tool invocation."""

    run_id: str = Field(min_length=1)
    step: int = Field(gt=0)
    tool_call_id: str = Field(min_length=1)
    metadata: JSONObject | None = None


class ToolExecutionResult(ProtocolModel):
    """The safe, normalized outcome of a local tool invocation."""

    content: JSONValue
    is_error: bool = False
    metadata: JSONObject | None = None


@runtime_checkable
class AgentTool(Protocol):
    """A registered asynchronous tool; providers never execute this contract."""

    @property
    def name(self) -> str:
        """Return a stable portable tool name."""

    @property
    def description(self) -> str:
        """Return a model-facing, non-empty description."""

    @property
    def input_schema(self) -> JSONObject:
        """Return an object-shaped JSON Schema for tool arguments."""

    @property
    def risk(self) -> ToolRisk:
        """Return conservative tool risk metadata."""

    async def execute(
        self, arguments: JSONObject, context: ToolExecutionContext
    ) -> ToolExecutionResult:
        """Execute one validated call without accessing unsafe host context."""
