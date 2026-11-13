"""Run input, immutable result snapshots, and per-run state definitions."""

from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from open_code_agent.agent.limits import AgentLimits
from open_code_agent.protocol import (
    AgentMessage,
    JSONObject,
    MessageRole,
    ModelError,
    TextContent,
    UsageInfo,
)
from open_code_agent.protocol.common import ProtocolModel


class AgentRunStatus(StrEnum):
    """Lifecycle state reported in immutable run snapshots."""

    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_MODEL = "waiting_for_model"
    EXECUTING_TOOLS = "executing_tools"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LIMIT_REACHED = "limit_reached"


class AgentRunRequest(ProtocolModel):
    """Provider-neutral input for one agent run.

    Initial conversation order is system prompt, supplied messages, then task.
    The runner deep-copies all supplied messages before it appends history.
    """

    model: str = Field(min_length=1)
    initial_messages: list[AgentMessage] = Field(default_factory=list)
    task: str | None = None
    system_prompt: str | None = None
    limits: AgentLimits = Field(default_factory=AgentLimits)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    stop_sequences: list[str] = Field(default_factory=list)
    metadata: JSONObject | None = None

    @field_validator("model")
    @classmethod
    def model_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Model must not be blank.")
        return value

    @field_validator("task", "system_prompt")
    @classmethod
    def optional_text_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Task and system_prompt must not be blank when supplied.")
        return value

    @field_validator("stop_sequences")
    @classmethod
    def stop_sequences_must_not_contain_empty_values(
        cls, value: list[str]
    ) -> list[str]:
        if any(sequence == "" for sequence in value):
            raise ValueError("Stop sequences must not contain empty strings.")
        return value

    def conversation(self) -> list[AgentMessage]:
        """Create a caller-isolated initial conversation in documented order."""
        messages: list[AgentMessage] = []
        if self.system_prompt is not None:
            messages.append(
                AgentMessage(
                    role=MessageRole.SYSTEM,
                    content=[TextContent(text=self.system_prompt)],
                )
            )
        messages.extend(
            message.model_copy(deep=True) for message in self.initial_messages
        )
        if self.task is not None:
            messages.append(
                AgentMessage(
                    role=MessageRole.USER, content=[TextContent(text=self.task)]
                )
            )
        if not messages:
            raise ValueError("An agent run requires at least one conversation message.")
        return messages


class AgentRunResult(ProtocolModel):
    """A detached structured terminal snapshot of one run."""

    run_id: str = Field(min_length=1)
    status: AgentRunStatus = Field(strict=False)
    model_steps: int = Field(ge=0)
    total_tool_calls: int = Field(ge=0)
    messages: list[AgentMessage] = Field(default_factory=list)
    usage: UsageInfo = Field(default_factory=UsageInfo)
    final_text: str | None = None
    error: ModelError | None = None
    limit_reason: str | None = None
    metadata: JSONObject | None = None

    @model_validator(mode="after")
    def terminal_details_match_status(self) -> "AgentRunResult":
        if self.status is AgentRunStatus.FAILED and self.error is None:
            raise ValueError("Failed runs require a normalized error.")
        if self.status is AgentRunStatus.LIMIT_REACHED and not self.limit_reason:
            raise ValueError("Limit-reached runs require a limit reason.")
        return self
