"""Normalized conversation messages and content parts."""

from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator

from open_code_agent.protocol.common import JSONObject, ProtocolModel
from open_code_agent.protocol.tools import ToolCallContent, ToolResultContent


class MessageRole(StrEnum):
    """Provider-neutral roles in a model conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ImageDetail(StrEnum):
    """Requested image fidelity where a provider supports it."""

    AUTO = "auto"
    LOW = "low"
    HIGH = "high"


class TextContent(ProtocolModel):
    """A text content part."""

    type: Literal["text"] = "text"
    text: str


class ImageContent(ProtocolModel):
    """An image referenced by a URL or a data URL."""

    type: Literal["image"] = "image"
    url: str = Field(min_length=1)
    media_type: str | None = None
    detail: ImageDetail | None = Field(default=None, strict=False)

    @field_validator("url")
    @classmethod
    def url_must_be_a_url_or_data_url(cls, value: str) -> str:
        """Accept absolute URLs and data URLs without fetching either."""
        if value.startswith("data:"):
            return value
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("Image URL must be an absolute URL or a data URL.")
        return value


type MessageContentPart = Annotated[
    TextContent | ImageContent | ToolCallContent | ToolResultContent,
    Field(discriminator="type"),
]


class AgentMessage(ProtocolModel):
    """A normalized message passed between an agent and a model adapter."""

    role: MessageRole = Field(strict=False)
    content: list[MessageContentPart] = Field(min_length=1)
    name: str | None = None
    metadata: JSONObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_content_for_role(self) -> "AgentMessage":
        """Ensure tool calls and results appear only in their normalized roles."""
        has_tool_call = any(isinstance(part, ToolCallContent) for part in self.content)
        has_tool_result = any(
            isinstance(part, ToolResultContent) for part in self.content
        )

        if self.role is MessageRole.TOOL:
            if not all(isinstance(part, ToolResultContent) for part in self.content):
                raise ValueError("Tool messages may contain only tool-result content.")
            return self
        if has_tool_result:
            raise ValueError("Only tool messages may contain tool-result content.")
        if self.role is not MessageRole.ASSISTANT and has_tool_call:
            raise ValueError("Only assistant messages may contain tool-call content.")
        return self
