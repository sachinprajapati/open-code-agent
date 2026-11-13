"""Tests for request mapping and incremental OpenAI-compatible SSE framing."""

import pytest

from open_code_agent.protocol import (
    AgentMessage,
    ImageContent,
    MessageRole,
    ModelRequest,
    TextContent,
    ToolCallContent,
    ToolDefinition,
    ToolResultContent,
)
from open_code_agent.providers.openai_compatible.errors import (
    OpenAICompatibleMappingError,
    OpenAICompatibleStreamError,
)
from open_code_agent.providers.openai_compatible.mapper import (
    OpenAICompatibleStreamMapper,
    map_chat_completions_request,
)
from open_code_agent.providers.openai_compatible.streaming import SSEEventParser


def test_request_mapping_covers_messages_tools_and_portable_controls() -> None:
    request = ModelRequest(
        model="test-model",
        messages=[
            AgentMessage(role=MessageRole.SYSTEM, content=[TextContent(text="System")]),
            AgentMessage(
                role=MessageRole.USER,
                content=[
                    TextContent(text="Describe"),
                    ImageContent(url="https://example.test/image.png"),
                ],
                metadata={"not": "sent"},
            ),
            AgentMessage(
                role=MessageRole.ASSISTANT,
                content=[
                    TextContent(text="Calling tool"),
                    ToolCallContent(
                        id="call-1", name="repo.read", arguments={"path": "a"}
                    ),
                ],
            ),
            AgentMessage(
                role=MessageRole.TOOL,
                content=[
                    ToolResultContent(
                        tool_call_id="call-1", name="repo.read", content={"text": "ok"}
                    )
                ],
            ),
        ],
        tools=[
            ToolDefinition(
                name="repo.read",
                description="Read a file.",
                input_schema={"type": "object", "properties": {}},
            )
        ],
        temperature=0.5,
        max_output_tokens=12,
        stop_sequences=["STOP"],
        metadata={"not": "sent"},
    )

    payload = map_chat_completions_request(request)

    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}
    assert payload["max_tokens"] == 12
    assert payload["stop"] == ["STOP"]
    assert "metadata" not in payload
    messages = payload["messages"]
    assert isinstance(messages, list)
    user_message = messages[1]
    assistant_message = messages[2]
    tool_message = messages[3]
    assert isinstance(user_message, dict)
    assert isinstance(assistant_message, dict)
    assert isinstance(tool_message, dict)
    user_content = user_message["content"]
    assistant_calls = assistant_message["tool_calls"]
    assert isinstance(user_content, list)
    assert isinstance(user_content[1], dict)
    assert user_content[1]["type"] == "image_url"
    assert isinstance(assistant_calls, list)
    assert isinstance(assistant_calls[0], dict)
    function = assistant_calls[0]["function"]
    assert isinstance(function, dict)
    assert function["arguments"] == '{"path":"a"}'
    assert tool_message["tool_call_id"] == "call-1"


def test_unsupported_assistant_image_is_rejected_before_network_io() -> None:
    request = ModelRequest(
        model="test-model",
        messages=[
            AgentMessage(
                role=MessageRole.ASSISTANT,
                content=[ImageContent(url="https://example.test/image.png")],
            )
        ],
    )

    with pytest.raises(OpenAICompatibleMappingError, match="Assistant image"):
        map_chat_completions_request(request)


def test_sse_parser_handles_boundaries_utf8_comments_done_and_limits() -> None:
    parser = SSEEventParser()
    payload = 'data: {"text":"hé"}\r\n\r\ndata: [DONE]\n\n'
    encoded = payload.encode("utf-8")
    split = encoded.index("é".encode()) + 1

    assert parser.feed(b": keepalive\n") == []
    assert parser.feed(encoded[:split]) == []
    assert parser.feed(encoded[split:]) == ['{"text":"hé"}', "[DONE]"]
    assert SSEEventParser().feed(b"data: one\ndata: two\n\n") == ["one\ntwo"]
    final_parser = SSEEventParser()
    assert final_parser.feed(b"data: final") == []
    assert final_parser.finalize() == ["final"]
    with pytest.raises(OpenAICompatibleStreamError, match="size limit"):
        SSEEventParser(max_event_size=3).feed(b"data: four")


def test_stream_mapper_preserves_tool_fragments_and_rejects_invalid_json() -> None:
    mapper = OpenAICompatibleStreamMapper("openai-compatible.test")
    first = mapper.consume(
        '{"id":"resp-1","model":"test","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"repo.read","arguments":"{\\"path\\":\\""}}]}}]}'
    )
    second = mapper.consume(
        '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"README.md\\"}"}}]},"finish_reason":"tool_calls"}]}'
    )

    assert [event.type for event in first + second] == [
        "stream_started",
        "tool_call_started",
        "tool_call_arguments_delta",
        "tool_call_arguments_delta",
        "tool_call_completed",
        "stream_completed",
    ]
    bad_mapper = OpenAICompatibleStreamMapper("openai-compatible.test")
    bad_mapper.consume(
        '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"repo.read","arguments":"[]"}}]}}]}'
    )
    with pytest.raises(OpenAICompatibleStreamError, match="JSON object"):
        bad_mapper.consume('{"choices":[{"index":0,"finish_reason":"tool_calls"}]}')
    with pytest.raises(OpenAICompatibleStreamError, match="malformed JSON"):
        OpenAICompatibleStreamMapper("openai-compatible.test").consume("{")
