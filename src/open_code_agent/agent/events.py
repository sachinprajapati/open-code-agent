"""Provider-neutral events emitted while an agent run progresses."""

from typing import Annotated, Literal

from pydantic import Field

from open_code_agent.agent.assembly import AgentStepResult
from open_code_agent.agent.state import AgentRunResult
from open_code_agent.protocol import ModelError, ModelStreamEvent, ToolResult
from open_code_agent.protocol.common import JSONObject, ProtocolModel


class AgentEvent(ProtocolModel):
    """Shared safe fields for a future CLI or editor consumer."""

    run_id: str = Field(min_length=1)
    step: int | None = Field(default=None, ge=1)
    metadata: JSONObject | None = None


class RunStarted(AgentEvent):
    type: Literal["run_started"] = "run_started"


class ModelStepStarted(AgentEvent):
    type: Literal["model_step_started"] = "model_step_started"


class ModelEventReceived(AgentEvent):
    type: Literal["model_event_received"] = "model_event_received"
    model_event: ModelStreamEvent


class ModelStepCompleted(AgentEvent):
    type: Literal["model_step_completed"] = "model_step_completed"
    step_result: AgentStepResult


class ToolExecutionStarted(AgentEvent):
    type: Literal["tool_execution_started"] = "tool_execution_started"
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)


class ToolExecutionCompleted(AgentEvent):
    type: Literal["tool_execution_completed"] = "tool_execution_completed"
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    result: ToolResult


class RunCompleted(AgentEvent):
    type: Literal["run_completed"] = "run_completed"
    result: AgentRunResult


class RunFailed(AgentEvent):
    type: Literal["run_failed"] = "run_failed"
    result: AgentRunResult
    error: ModelError


class RunLimitReached(AgentEvent):
    type: Literal["run_limit_reached"] = "run_limit_reached"
    result: AgentRunResult


type AgentRunEvent = Annotated[
    RunStarted
    | ModelStepStarted
    | ModelEventReceived
    | ModelStepCompleted
    | ToolExecutionStarted
    | ToolExecutionCompleted
    | RunCompleted
    | RunFailed
    | RunLimitReached,
    Field(discriminator="type"),
]
