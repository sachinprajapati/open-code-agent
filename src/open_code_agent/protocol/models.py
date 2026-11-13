"""Provider-neutral model capabilities, descriptors, and requests."""

from pydantic import Field, field_validator, model_validator

from open_code_agent.protocol.common import JSONObject, ProtocolModel
from open_code_agent.protocol.messages import AgentMessage
from open_code_agent.protocol.tools import ToolDefinition


class ModelCapabilities(ProtocolModel):
    """Known, provider-agnostic capabilities for a model.

    Every capability defaults to false so adapters must explicitly establish it.
    """

    native_tool_calling: bool = False
    parallel_tool_calling: bool = False
    image_input: bool = False
    reasoning_output: bool = False
    prompt_caching: bool = False
    streaming: bool = False
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)


class ModelInfo(ProtocolModel):
    """Provider-neutral identifying information for a model."""

    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    capabilities: ModelCapabilities
    metadata: JSONObject | None = None

    @field_validator("id", "display_name", "provider")
    @classmethod
    def strings_must_not_be_blank(cls, value: str) -> str:
        """Reject whitespace-only identifiers and display names."""
        if not value.strip():
            raise ValueError("Model identifying fields must not be blank.")
        return value


class ModelRequest(ProtocolModel):
    """A provider-neutral request for a model response.

    Temperature uses the portable range 0.0 through 2.0. Providers with a
    narrower range must validate or translate it in their future adapter.
    """

    model: str = Field(min_length=1)
    messages: list[AgentMessage] = Field(min_length=1)
    tools: list[ToolDefinition] = Field(default_factory=list)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    stop_sequences: list[str] = Field(default_factory=list)
    stream: bool = False
    metadata: JSONObject | None = None

    @field_validator("model")
    @classmethod
    def model_must_not_be_blank(cls, value: str) -> str:
        """Reject whitespace-only model identifiers."""
        if not value.strip():
            raise ValueError("Model must not be blank.")
        return value

    @field_validator("stop_sequences")
    @classmethod
    def stop_sequences_must_not_contain_empty_values(
        cls, value: list[str]
    ) -> list[str]:
        """Reject empty stopping sequences."""
        if any(sequence == "" for sequence in value):
            raise ValueError("Stop sequences must not contain empty strings.")
        return value

    @model_validator(mode="after")
    def tool_names_must_be_unique(self) -> "ModelRequest":
        """Reject ambiguous duplicate tool definitions."""
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("Tool names must be unique within a model request.")
        return self


class UsageInfo(ProtocolModel):
    """Provider-reported token accounting.

    ``total_tokens`` is accepted as reported by the provider rather than
    derived: cached and reasoning token semantics vary and may overlap input or
    output counts. This avoids silently double-counting those categories.
    """

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
