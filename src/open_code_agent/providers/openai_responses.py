"""Native OpenAI Responses API provider with streaming function calls."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

import httpx

from open_code_agent.protocol import (
    FinishReason,
    ImageContent,
    ModelCapabilities,
    ModelError,
    ModelErrorCategory,
    ModelInfo,
    ModelRequest,
    ModelStreamEvent,
    StreamCompleted,
    StreamError,
    StreamStarted,
    TextContent,
    TextDelta,
    ToolCall,
    ToolCallArgumentsDelta,
    ToolCallCompleted,
    ToolCallContent,
    ToolCallStarted,
    ToolResultContent,
    UsageInfo,
    UsageReported,
)
from open_code_agent.providers.base import ConfigurationValidationResult
from open_code_agent.providers.exceptions import ProviderError
from open_code_agent.providers.openai_compatible.errors import (
    OpenAICompatibleMappingError,
    OpenAICompatibleRequestError,
    OpenAICompatibleStreamError,
)
from open_code_agent.providers.openai_compatible.provider import (
    OpenAICompatibleProvider,
)
from open_code_agent.providers.openai_compatible.streaming import SSEEventParser
from open_code_agent.providers.profiles import OpenAICompatibleProfile
from open_code_agent.providers.secrets import (
    EnvironmentSecretResolver,
    SecretResolutionError,
    SecretResolver,
)


def _json_text(value: object) -> str:
    return (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    )


def map_responses_request(request: ModelRequest) -> dict[str, object]:
    """Map the neutral conversation contract to the Responses API input items."""
    input_items: list[dict[str, object]] = []
    for message in request.messages:
        if message.role.value == "tool":
            for part in message.content:
                if not isinstance(part, ToolResultContent):
                    raise OpenAICompatibleMappingError(
                        "Tool messages require tool results."
                    )
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": part.tool_call_id,
                        "output": _json_text(part.content),
                    }
                )
            continue
        if message.role.value == "assistant":
            text = "".join(
                part.text for part in message.content if isinstance(part, TextContent)
            )
            if text:
                input_items.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                )
            for part in message.content:
                if isinstance(part, ToolCallContent):
                    input_items.append(
                        {
                            "type": "function_call",
                            "call_id": part.id,
                            "name": part.name,
                            "arguments": _json_text(part.arguments),
                        }
                    )
                elif not isinstance(part, TextContent):
                    raise OpenAICompatibleMappingError(
                        "Assistant content is unsupported by Responses."
                    )
            continue
        content: list[dict[str, object]] = []
        for part in message.content:
            if isinstance(part, TextContent):
                content.append({"type": "input_text", "text": part.text})
            elif isinstance(part, ImageContent):
                image: dict[str, object] = {
                    "type": "input_image",
                    "image_url": part.url,
                }
                if part.detail is not None:
                    image["detail"] = part.detail.value
                content.append(image)
            else:
                raise OpenAICompatibleMappingError(
                    "Prompt content is unsupported by Responses."
                )
        input_items.append({"role": message.role.value, "content": content})
    payload: dict[str, object] = {
        "model": request.model,
        "input": input_items,
        "stream": True,
    }
    if request.tools:
        payload["tools"] = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
                "strict": False,
            }
            for tool in request.tools
        ]
        payload["parallel_tool_calls"] = False
    if request.max_output_tokens is not None:
        payload["max_output_tokens"] = request.max_output_tokens
    return payload


@dataclass
class _Call:
    item_id: str
    call_id: str
    name: str
    index: int
    arguments: list[str] = field(default_factory=list)


class _ResponsesStreamMapper:
    def __init__(self) -> None:
        self.started = False
        self.terminal = False
        self.calls: dict[str, _Call] = {}
        self.calls_by_item_id: dict[str, _Call] = {}

    def consume(self, data: str) -> list[ModelStreamEvent]:
        try:
            event = json.loads(data)
        except json.JSONDecodeError as error:
            raise OpenAICompatibleStreamError(
                "Responses stream event contains malformed JSON."
            ) from error
        if not isinstance(event, dict):
            raise OpenAICompatibleStreamError(
                "Responses stream event must be an object."
            )
        kind = event.get("type")
        if kind == "response.created":
            response = event.get("response")
            if not isinstance(response, dict):
                raise OpenAICompatibleStreamError(
                    "Responses created event requires a response."
                )
            return self._start(response.get("id"), response.get("model"))
        events = self._start(None, None)
        if kind == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                events.append(TextDelta(text=delta))
        elif kind == "response.output_item.added":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                item_id = item.get("id")
                call_id, name = item.get("call_id"), item.get("name")
                index = event.get("output_index", 0)
                if (
                    not isinstance(item_id, str)
                    or not isinstance(call_id, str)
                    or not isinstance(name, str)
                    or not isinstance(index, int)
                ):
                    raise OpenAICompatibleStreamError(
                        "Responses function call lacks an ID, name, or index."
                    )
                new_call = _Call(item_id, call_id, name, index)
                self.calls[call_id] = new_call
                self.calls_by_item_id[item_id] = new_call
                events.append(
                    ToolCallStarted(tool_call_id=call_id, name=name, index=index)
                )
        elif kind == "response.function_call_arguments.delta":
            call_id, delta = event.get("call_id"), event.get("delta")
            argument_call: _Call | None = (
                self.calls.get(call_id) if isinstance(call_id, str) else None
            )
            if argument_call is None:
                item_id = event.get("item_id")
                argument_call = (
                    self.calls_by_item_id.get(item_id)
                    if isinstance(item_id, str)
                    else None
                )
            if argument_call is None or not isinstance(delta, str):
                raise OpenAICompatibleStreamError(
                    "Responses function-call arguments lack a known call ID."
                )
            argument_call.arguments.append(delta)
            events.append(
                ToolCallArgumentsDelta(
                    tool_call_id=argument_call.call_id,
                    arguments_delta=delta,
                    index=argument_call.index,
                )
            )
        elif kind == "response.function_call_arguments.done":
            call_id, arguments = event.get("call_id"), event.get("arguments")
            completed_call: _Call | None = (
                self.calls.get(call_id) if isinstance(call_id, str) else None
            )
            if completed_call is None:
                item_id = event.get("item_id")
                completed_call = (
                    self.calls_by_item_id.get(item_id)
                    if isinstance(item_id, str)
                    else None
                )
            if completed_call is None or not isinstance(arguments, str):
                raise OpenAICompatibleStreamError(
                    "Responses function-call completion lacks a known call ID."
                )
            if not completed_call.arguments:
                completed_call.arguments.append(arguments)
                events.append(
                    ToolCallArgumentsDelta(
                        tool_call_id=completed_call.call_id,
                        arguments_delta=arguments,
                        index=completed_call.index,
                    )
                )
        elif kind == "response.completed":
            response = event.get("response")
            if not isinstance(response, dict):
                raise OpenAICompatibleStreamError(
                    "Responses completion event requires a response."
                )
            events.extend(self._usage(response.get("usage")))
            events.extend(
                self._complete(
                    FinishReason.TOOL_CALLS if self.calls else FinishReason.STOP,
                    "completed",
                )
            )
        elif kind in {"response.failed", "error"}:
            message = event.get("message")
            raise OpenAICompatibleStreamError(
                message if isinstance(message, str) else "Responses API stream failed."
            )
        return events

    def finish(self) -> list[ModelStreamEvent]:
        return (
            []
            if self.terminal
            else self._start(None, None) + self._complete(FinishReason.UNKNOWN, None)
        )

    def _start(self, request_id: object, model: object) -> list[ModelStreamEvent]:
        if self.started:
            return []
        self.started = True
        return [
            StreamStarted(
                provider_request_id=request_id if isinstance(request_id, str) else None,
                model=model if isinstance(model, str) else None,
            )
        ]

    def _complete(
        self, reason: FinishReason, provider_reason: str | None
    ) -> list[ModelStreamEvent]:
        if self.terminal:
            return []
        events: list[ModelStreamEvent] = []
        for call in self.calls.values():
            try:
                arguments = json.loads("".join(call.arguments))
            except json.JSONDecodeError as error:
                raise OpenAICompatibleStreamError(
                    "Responses function call has malformed JSON arguments."
                ) from error
            if not isinstance(arguments, dict):
                raise OpenAICompatibleStreamError(
                    "Responses function-call arguments must be an object."
                )
            events.append(
                ToolCallCompleted(
                    tool_call=ToolCall(
                        id=call.call_id, name=call.name, arguments=arguments
                    )
                )
            )
        self.terminal = True
        events.append(
            StreamCompleted(
                finish_reason=reason, provider_finish_reason=provider_reason
            )
        )
        return events

    @staticmethod
    def _usage(value: object) -> list[ModelStreamEvent]:
        if not isinstance(value, dict):
            return []

        def integer(key: str) -> int:
            item = value.get(key, 0)
            return item if isinstance(item, int) and item >= 0 else 0

        input_details = value.get("input_tokens_details")
        output_details = value.get("output_tokens_details")
        cached = (
            input_details.get("cached_tokens", 0)
            if isinstance(input_details, dict)
            else 0
        )
        reasoning = (
            output_details.get("reasoning_tokens", 0)
            if isinstance(output_details, dict)
            else 0
        )
        return [
            UsageReported(
                usage=UsageInfo(
                    input_tokens=integer("input_tokens"),
                    output_tokens=integer("output_tokens"),
                    total_tokens=integer("total_tokens"),
                    cached_input_tokens=cached
                    if isinstance(cached, int) and cached >= 0
                    else 0,
                    reasoning_tokens=reasoning
                    if isinstance(reasoning, int) and reasoning >= 0
                    else 0,
                )
            )
        ]


class OpenAIResponsesProvider:
    """OpenAI's Responses API, used for reasoning models with local tools."""

    def __init__(
        self,
        profile: OpenAICompatibleProfile,
        *,
        secret_resolver: SecretResolver | None = None,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_sse_event_size: int = 1_048_576,
    ) -> None:
        if profile.api != "responses":
            raise ValueError("OpenAIResponsesProvider requires a responses profile.")
        if client is not None and transport is not None:
            raise ValueError("Provide either client or transport, not both.")
        self._profile = profile
        self._secret_resolver = secret_resolver or EnvironmentSecretResolver()
        self._client = client or httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(
                profile.request_timeout_seconds, connect=profile.connect_timeout_seconds
            ),
        )
        self._owns_client = client is None
        self._closed = False
        self._max_sse_event_size = max_sse_event_size

    @property
    def provider_id(self) -> str:
        return f"openai-responses.{self._profile.profile_id}"

    @property
    def display_name(self) -> str:
        return self._profile.display_name

    async def __aenter__(self) -> Self:
        self._ensure_open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            if self._owns_client:
                await self._client.aclose()

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        return self._stream(request)

    async def _stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        self._ensure_open()
        try:
            headers = await self._headers("text/event-stream")
            payload = map_responses_request(request)
            mapper, parser = (
                _ResponsesStreamMapper(),
                SSEEventParser(self._max_sse_event_size),
            )
            async with self._client.stream(
                "POST",
                self._profile.endpoint("responses"),
                json=payload,
                headers=headers,
            ) as response:
                if not response.is_success:
                    yield StreamError(
                        error=await self._response_error(response, True, headers)
                    )
                    return
                async for chunk in response.aiter_bytes():
                    for data in parser.feed(chunk):
                        for event in mapper.consume(data):
                            yield event
                        if mapper.terminal:
                            return
                for event in mapper.finish():
                    yield event
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException:
            yield StreamError(
                error=self._local_error(
                    ModelErrorCategory.TIMEOUT, "Request timed out.", True
                )
            )
        except httpx.RequestError:
            yield StreamError(
                error=self._local_error(
                    ModelErrorCategory.CONNECTION, "Connection failed.", True
                )
            )
        except OpenAICompatibleStreamError as error:
            yield StreamError(
                error=self._local_error(
                    ModelErrorCategory.PROVIDER_ERROR, str(error), False
                )
            )
        except SecretResolutionError as error:
            yield StreamError(
                error=self._local_error(
                    ModelErrorCategory.AUTHENTICATION, str(error), False
                )
            )
        except OpenAICompatibleMappingError as error:
            yield StreamError(
                error=self._local_error(
                    ModelErrorCategory.INVALID_REQUEST, str(error), False
                )
            )

    async def list_models(self) -> tuple[ModelInfo, ...]:
        self._ensure_open()
        headers = await self._headers("application/json")
        try:
            response = await self._client.get(
                self._profile.endpoint("models"), headers=headers
            )
        except httpx.TimeoutException as error:
            raise OpenAICompatibleRequestError(
                self._local_error(
                    ModelErrorCategory.TIMEOUT, "Model listing timed out.", True
                )
            ) from error
        except httpx.RequestError as error:
            raise OpenAICompatibleRequestError(
                self._local_error(
                    ModelErrorCategory.CONNECTION,
                    "Model listing connection failed.",
                    True,
                )
            ) from error
        if not response.is_success:
            raise OpenAICompatibleRequestError(
                await self._response_error(response, False, headers)
            )
        try:
            data = response.json().get("data")
        except (json.JSONDecodeError, AttributeError) as error:
            raise OpenAICompatibleMappingError(
                "Models endpoint returned malformed JSON."
            ) from error
        if not isinstance(data, list):
            raise OpenAICompatibleMappingError(
                "Models endpoint response must contain a data array."
            )
        return tuple(
            sorted(
                (
                    ModelInfo(
                        id=item["id"],
                        display_name=item["id"],
                        provider=self.provider_id,
                        capabilities=ModelCapabilities(
                            native_tool_calling=True,
                            streaming=True,
                            reasoning_output=True,
                        ),
                    )
                    for item in data
                    if isinstance(item, dict)
                    and isinstance(item.get("id"), str)
                    and item["id"].strip()
                ),
                key=lambda model: model.id,
            )
        )

    async def validate_configuration(self) -> ConfigurationValidationResult:
        try:
            models = await self.list_models()
        except OpenAICompatibleRequestError as error:
            return ConfigurationValidationResult(valid=False, error=error.model_error)
        except SecretResolutionError as error:
            return ConfigurationValidationResult(valid=False, diagnostic=str(error))
        except (OpenAICompatibleMappingError, ProviderError) as error:
            return ConfigurationValidationResult(valid=False, diagnostic=str(error))
        return ConfigurationValidationResult(
            valid=True, metadata={"model_count": len(models)}
        )

    async def _headers(self, accept: str) -> dict[str, str]:
        headers = {
            "Accept": accept,
            "Content-Type": "application/json",
            **self._profile.extra_headers,
        }
        if self._profile.api_key_secret_ref is not None:
            secret = await self._secret_resolver.resolve(
                self._profile.api_key_secret_ref
            )
            headers["Authorization"] = f"Bearer {secret}"
        if self._profile.organization is not None:
            headers["OpenAI-Organization"] = self._profile.organization
        if self._profile.project is not None:
            headers["OpenAI-Project"] = self._profile.project
        return headers

    async def _response_error(
        self,
        response: httpx.Response,
        not_found_is_model: bool,
        headers: dict[str, str],
    ) -> ModelError:
        body = await response.aread()
        message, code = OpenAICompatibleProvider._safe_error_details(
            body, OpenAICompatibleProvider._sensitive_values(headers)
        )
        status = response.status_code
        return ModelError(
            category=OpenAICompatibleProvider._status_category(
                status, not_found_is_model
            ),
            message=message or f"Provider returned HTTP {status}.",
            retryable=status == 429 or status >= 500,
            provider=self.provider_id,
            provider_error_code=code,
            http_status=status,
            retry_after_seconds=OpenAICompatibleProvider._retry_after(
                response.headers.get("Retry-After")
            ),
        )

    def _local_error(
        self, category: ModelErrorCategory, message: str, retryable: bool
    ) -> ModelError:
        return ModelError(
            category=category,
            message=message,
            retryable=retryable,
            provider=self.provider_id,
        )

    def _ensure_open(self) -> None:
        if self._closed or self._client.is_closed:
            raise ProviderError("OpenAI Responses provider is closed.")
