"""Validated limits for one bounded agent execution."""

from pydantic import Field

from open_code_agent.protocol.common import ProtocolModel


class AgentLimits(ProtocolModel):
    """Hard per-run bounds; all tool calls, including invalid ones, count."""

    max_model_steps: int = Field(default=30, gt=0)
    max_tool_calls_per_step: int = Field(default=8, gt=0)
    max_total_tool_calls: int = Field(default=32, gt=0)
    overall_timeout_seconds: float | None = Field(default=None, gt=0)
