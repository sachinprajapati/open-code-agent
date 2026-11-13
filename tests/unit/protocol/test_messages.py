"""Tests for normalized message content and role validation."""

import pytest
from pydantic import ValidationError

from open_code_agent.protocol import (
    AgentMessage,
    ImageContent,
    ImageDetail,
    MessageRole,
    TextContent,
    ToolCallContent,
    ToolResultContent,
)


def test_valid_content_parts_and_roles() -> None:
    system_message = AgentMessage(
        role=MessageRole.SYSTEM,
        content=[TextContent(text="Follow the repository policy.")],
    )
    user_message = AgentMessage(
        role=MessageRole.USER,
        content=[
            TextContent(text="Describe this image."),
            ImageContent(
                url="data:image/png;base64,AAAA",
                media_type="image/png",
                detail=ImageDetail.LOW,
            ),
        ],
    )
    assistant_message = AgentMessage(
        role=MessageRole.ASSISTANT,
        content=[
            TextContent(text="I will inspect it."),
            ToolCallContent(id="call-1", name="inspect.image", arguments={"id": 1}),
        ],
    )
    tool_message = AgentMessage(
        role=MessageRole.TOOL,
        content=[
            ToolResultContent(
                tool_call_id="call-1",
                name="inspect.image",
                content={"labels": ["diagram", "text"]},
            )
        ],
    )

    assert isinstance(system_message.content[0], TextContent)
    assert system_message.content[0].text == "Follow the repository policy."
    assert isinstance(user_message.content[1], ImageContent)
    assert isinstance(assistant_message.content[1], ToolCallContent)
    assert isinstance(tool_message.content[0], ToolResultContent)


@pytest.mark.parametrize(
    ("role", "content", "error"),
    [
        (
            MessageRole.SYSTEM,
            [ToolResultContent(tool_call_id="call-1", name="read", content="no")],
            "Only tool messages",
        ),
        (
            MessageRole.USER,
            [ToolCallContent(id="call-1", name="read", arguments={})],
            "Only assistant messages",
        ),
        (
            MessageRole.ASSISTANT,
            [ToolResultContent(tool_call_id="call-1", name="read", content="no")],
            "Only tool messages",
        ),
        (
            MessageRole.TOOL,
            [TextContent(text="not a result")],
            "Tool messages may contain only",
        ),
    ],
)
def test_invalid_role_content_combinations_are_rejected(
    role: MessageRole, content: list[object], error: str
) -> None:
    with pytest.raises(ValidationError, match=error):
        AgentMessage.model_validate({"role": role, "content": content})


def test_empty_message_content_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        AgentMessage(role=MessageRole.USER, content=[])


def test_message_round_trip_preserves_discriminated_content() -> None:
    message = AgentMessage(
        role=MessageRole.ASSISTANT,
        content=[
            ToolCallContent(id="call-2", name="repo.status", arguments={"short": True})
        ],
        metadata={"source": "test"},
    )

    restored = AgentMessage.model_validate(message.model_dump(mode="json"))

    assert restored == message
    assert isinstance(restored.content[0], ToolCallContent)
