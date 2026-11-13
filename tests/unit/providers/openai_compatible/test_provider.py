"""Network-free HTTP, lifecycle, and registry tests for the compatible provider."""

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from open_code_agent.protocol import (
    AgentMessage,
    MessageRole,
    ModelErrorCategory,
    ModelRequest,
    ModelStreamEvent,
    StreamCompleted,
    StreamError,
    TextContent,
    TextDelta,
    ToolCallArgumentsDelta,
    UsageReported,
)
from open_code_agent.providers import (
    MappingSecretResolver,
    ModelProviderRegistry,
    OpenAICompatibleProfile,
    OpenAICompatibleProvider,
)
from open_code_agent.providers.exceptions import ProviderError


def profile(**overrides: object) -> OpenAICompatibleProfile:
    """Build a profile for a mock HTTP endpoint."""
    values: dict[str, object] = {
        "profile_id": "test",
        "display_name": "Test compatible endpoint",
        "base_url": "https://example.test/v1",
        "default_model": "test-model",
    }
    values.update(overrides)
    return OpenAICompatibleProfile.model_validate(values)


def request() -> ModelRequest:
    """Build the minimal valid request used by streaming tests."""
    return ModelRequest(
        model="test-model",
        messages=[
            AgentMessage(role=MessageRole.USER, content=[TextContent(text="Hello")])
        ],
    )


async def collect(stream: AsyncIterator[ModelStreamEvent]) -> list[ModelStreamEvent]:
    """Collect a stream without reconstructing an assistant response."""
    return [event async for event in stream]


def sse(*payloads: str) -> bytes:
    """Build a compact SSE body from JSON payloads or the done sentinel."""
    return "".join(f"data: {payload}\n\n" for payload in payloads).encode()


class DelayedByteStream(httpx.AsyncByteStream):
    """A cancellable response stream that records whether it was closed."""

    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        await asyncio.sleep(1)
        yield sse("[DONE]")

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_streaming_maps_text_usage_headers_and_tool_fragments() -> None:
    captured: list[httpx.Request] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        captured.append(http_request)
        return httpx.Response(
            200,
            content=sse(
                '{"id":"response-1","model":"test-model","choices":[{"index":0,"delta":{"content":"Hi"}}]}',
                '{"choices":[{"index":0,"delta":{"content":""}}],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}',
                '{"choices":[{"index":0,"finish_reason":"stop"}]}',
                "[DONE]",
            ),
        )

    provider = OpenAICompatibleProvider(
        profile(
            api_key_secret_ref="host:key",
            organization="organization-id",
            project="project-id",
        ),
        secret_resolver=MappingSecretResolver({"host:key": "not-a-real-secret"}),
        transport=httpx.MockTransport(handler),
    )
    events = await collect(provider.stream(request()))

    assert [event.type for event in events] == [
        "stream_started",
        "text_delta",
        "usage_reported",
        "stream_completed",
    ]
    assert isinstance(events[1], TextDelta)
    assert isinstance(events[2], UsageReported)
    assert isinstance(events[-1], StreamCompleted)
    assert captured[0].url.path == "/v1/chat/completions"
    assert captured[0].headers["Authorization"] == "Bearer not-a-real-secret"
    assert captured[0].headers["OpenAI-Organization"] == "organization-id"
    assert captured[0].headers["OpenAI-Project"] == "project-id"
    await provider.aclose()


@pytest.mark.asyncio
async def test_streaming_normalizes_status_errors_and_redacts_secret() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "2"},
            json={
                "error": {"message": "bad secret-value\nplease retry", "code": "limit"}
            },
        )

    provider = OpenAICompatibleProvider(
        profile(api_key_secret_ref="host:key"),
        secret_resolver=MappingSecretResolver({"host:key": "secret-value"}),
        transport=httpx.MockTransport(handler),
    )
    events = await collect(provider.stream(request()))

    assert len(events) == 1
    assert isinstance(events[0], StreamError)
    assert events[0].error.category is ModelErrorCategory.RATE_LIMIT
    assert events[0].error.retry_after_seconds == 2
    assert "secret-value" not in events[0].error.message
    assert "\n" not in events[0].error.message
    await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "category"),
    [
        (400, ModelErrorCategory.INVALID_REQUEST),
        (401, ModelErrorCategory.AUTHENTICATION),
        (403, ModelErrorCategory.PERMISSION),
        (404, ModelErrorCategory.MODEL_NOT_FOUND),
        (408, ModelErrorCategory.TIMEOUT),
        (422, ModelErrorCategory.INVALID_REQUEST),
        (500, ModelErrorCategory.PROVIDER_ERROR),
    ],
)
async def test_streaming_status_categories(
    status: int, category: ModelErrorCategory
) -> None:
    provider = OpenAICompatibleProvider(
        profile(), transport=httpx.MockTransport(lambda _: httpx.Response(status))
    )

    events = await collect(provider.stream(request()))

    assert isinstance(events[0], StreamError)
    assert events[0].error.category is category
    await provider.aclose()


@pytest.mark.asyncio
async def test_model_listing_validation_lifecycle_and_registry_integration() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        if http_request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "z"}, {"id": "a"}]})
        return httpx.Response(200, content=sse("[DONE]"))

    caller_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(profile(), client=caller_client)
    models = await provider.list_models()
    validation = await provider.validate_configuration()
    registry = ModelProviderRegistry()
    second = OpenAICompatibleProvider(
        profile(profile_id="second"), transport=httpx.MockTransport(handler)
    )
    registry.register(provider)
    registry.register(second)
    await provider.aclose()

    assert [model.id for model in models] == ["a", "z"]
    assert not models[0].capabilities.image_input
    assert validation.valid
    assert tuple(item.provider_id for item in registry.list_providers()) == (
        "openai-compatible.second",
        "openai-compatible.test",
    )
    assert not caller_client.is_closed
    with pytest.raises(ProviderError, match="closed"):
        await collect(provider.stream(request()))
    await caller_client.aclose()
    await second.aclose()


@pytest.mark.asyncio
async def test_validation_reports_models_errors_and_connection_errors() -> None:
    unauthorized = OpenAICompatibleProvider(
        profile(), transport=httpx.MockTransport(lambda _: httpx.Response(401))
    )
    disconnected = OpenAICompatibleProvider(
        profile(),
        transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(
                httpx.ConnectError("offline", request=request)
            )
        ),
    )
    missing_models = OpenAICompatibleProvider(
        profile(), transport=httpx.MockTransport(lambda _: httpx.Response(404))
    )

    unauthorized_result = await unauthorized.validate_configuration()
    disconnected_result = await disconnected.validate_configuration()
    missing_models_result = await missing_models.validate_configuration()

    assert not unauthorized_result.valid
    assert unauthorized_result.error is not None
    assert unauthorized_result.error.category is ModelErrorCategory.AUTHENTICATION
    assert not disconnected_result.valid
    assert disconnected_result.error is not None
    assert disconnected_result.error.category is ModelErrorCategory.CONNECTION
    assert missing_models_result.diagnostic is not None
    assert "/models" in missing_models_result.diagnostic
    await unauthorized.aclose()
    await disconnected.aclose()
    await missing_models.aclose()


@pytest.mark.asyncio
async def test_tool_streaming_preserves_fragments_and_completes_once() -> None:
    provider = OpenAICompatibleProvider(
        profile(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=sse(
                    '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"repo.read","arguments":"{\\"path\\":\\""}}]}}]}',
                    '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"README.md\\"}"}}]},"finish_reason":"tool_calls"}]}',
                ),
            )
        ),
    )

    events = await collect(provider.stream(request()))
    fragments = [
        event.arguments_delta
        for event in events
        if isinstance(event, ToolCallArgumentsDelta)
    ]

    assert fragments == ['{"path":"', 'README.md"}']
    assert [event.type for event in events].count("stream_completed") == 1
    assert [event.type for event in events].count("stream_error") == 0
    await provider.aclose()


@pytest.mark.asyncio
async def test_cancellation_propagates_and_closes_the_response_stream() -> None:
    delayed_stream = DelayedByteStream()
    provider = OpenAICompatibleProvider(
        profile(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, stream=delayed_stream)
        ),
    )
    task = asyncio.create_task(collect(provider.stream(request())))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert delayed_stream.closed
    await provider.aclose()
