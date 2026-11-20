import json

import httpx
import pytest

from open_code_agent.protocol import (
    AgentMessage,
    MessageRole,
    ModelRequest,
    TextContent,
    ToolDefinition,
)
from open_code_agent.providers import MappingSecretResolver, OpenAICompatibleProfile
from open_code_agent.providers.openai_responses import OpenAIResponsesProvider


def profile() -> OpenAICompatibleProfile:
    return OpenAICompatibleProfile(
        profile_id="openai",
        display_name="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-6-luna",
        api_key_secret_ref="env:OPENAI_API_KEY",
        api="responses",
    )


@pytest.mark.asyncio
async def test_responses_provider_streams_function_calls() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["payload"] = json.loads(request.content)
        events = [
            {
                "type": "response.created",
                "response": {"id": "resp_1", "model": "gpt-6-luna"},
            },
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "list_files",
                },
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_1",
                "delta": '{"path":"."}',
            },
            {
                "type": "response.completed",
                "response": {
                    "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
                },
            },
        ]
        stream = (
            b"\n\n".join(
                f"data: {json.dumps(event, separators=(',', ':'))}".encode()
                for event in events
            )
            + b"\n\n"
        )
        return httpx.Response(
            200, content=stream, headers={"content-type": "text/event-stream"}
        )

    provider = OpenAIResponsesProvider(
        profile(),
        secret_resolver=MappingSecretResolver({"env:OPENAI_API_KEY": "test-key"}),
        transport=httpx.MockTransport(handler),
    )
    request = ModelRequest(
        model="gpt-6-luna",
        messages=[
            AgentMessage(role=MessageRole.USER, content=[TextContent(text="inspect")])
        ],
        tools=[
            ToolDefinition(
                name="list_files",
                description="List files",
                input_schema={"type": "object"},
            )
        ],
        stream=True,
    )
    events = [event async for event in provider.stream(request)]
    await provider.aclose()

    assert captured["path"] == "/v1/responses"
    assert captured["payload"] == {
        "model": "gpt-6-luna",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "inspect"}]}
        ],
        "stream": True,
        "tools": [
            {
                "type": "function",
                "name": "list_files",
                "description": "List files",
                "parameters": {"type": "object"},
                "strict": False,
            }
        ],
        "parallel_tool_calls": False,
    }
    assert events[1].type == "tool_call_started"
    completed = next(event for event in events if event.type == "tool_call_completed")
    assert completed.tool_call.arguments == {"path": "."}
    assert events[-1].type == "stream_completed"
