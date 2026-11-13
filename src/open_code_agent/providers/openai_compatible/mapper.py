"""Request and streaming-response translation for Chat Completions."""

import json
from dataclasses import dataclass, field

from open_code_agent.protocol import (
    AgentMessage,
    FinishReason,
    ImageContent,
    JSONObject,
    JSONValue,
    ModelRequest,
    ModelStreamEvent,
    StreamCompleted,
    StreamStarted,
    TextContent,
    TextDelta,
    ToolCall,
    ToolCallArgumentsDelta,
    ToolCallCompleted,
    ToolCallContent,
    ToolCallStarted,
    ToolDefinition,
    ToolResultContent,
    UsageInfo,
    UsageReported,
)
from open_code_agent.providers.openai_compatible.errors import (
    OpenAICompatibleMappingError,
    OpenAICompatibleStreamError,
)


def map_chat_completions_request(request: ModelRequest) -> JSONObject:
    """Map a normalized request to the portable Chat Completions subset.

    The adapter uses ``max_tokens`` because it remains the most broadly
    supported output limit field among OpenAI-compatible servers. Metadata is
    deliberately not transmitted.
    """
    messages: list[JSONValue] = [_map_message(message) for message in request.messages]
    payload: JSONObject = {
        "model": request.model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if request.tools:
        payload["tools"] = [_map_tool_definition(tool) for tool in request.tools]
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        payload["max_tokens"] = request.max_output_tokens
    if request.stop_sequences:
        payload["stop"] = list(request.stop_sequences)
    return payload


def _map_message(message: AgentMessage) -> JSONObject:
    mapped: JSONObject = {"role": message.role.value}
    if message.name is not None:
        mapped["name"] = message.name

    if message.role.value == "tool":
        return _map_tool_result_message(message, mapped)
    if message.role.value == "assistant":
        return _map_assistant_message(message, mapped)
    return _map_prompt_message(message, mapped)


def _map_prompt_message(message: AgentMessage, mapped: JSONObject) -> JSONObject:
    content_parts: list[JSONValue] = []
    has_image = False
    for part in message.content:
        if isinstance(part, TextContent):
            content_parts.append({"type": "text", "text": part.text})
        elif isinstance(part, ImageContent):
            has_image = True
            image_url: JSONObject = {"url": part.url}
            if part.detail is not None:
                image_url["detail"] = part.detail.value
            content_parts.append({"type": "image_url", "image_url": image_url})
        else:
            raise OpenAICompatibleMappingError(
                "Prompt messages contain content unsupported by Chat Completions."
            )
    if not has_image:
        mapped["content"] = "".join(_text_from_part(part) for part in message.content)
    else:
        mapped["content"] = content_parts
    return mapped


def _map_assistant_message(message: AgentMessage, mapped: JSONObject) -> JSONObject:
    text_parts: list[str] = []
    tool_calls: list[JSONValue] = []
    for part in message.content:
        if isinstance(part, TextContent):
            text_parts.append(part.text)
        elif isinstance(part, ToolCallContent):
            tool_calls.append(
                {
                    "id": part.id,
                    "type": "function",
                    "function": {
                        "name": part.name,
                        "arguments": _json_text(part.arguments),
                    },
                }
            )
        elif isinstance(part, ImageContent):
            raise OpenAICompatibleMappingError(
                "Assistant image content is not representable by Chat Completions."
            )
        else:
            raise OpenAICompatibleMappingError(
                "Assistant tool-result content is not representable in an "
                "assistant message."
            )
    mapped["content"] = "".join(text_parts) if text_parts else None
    if tool_calls:
        mapped["tool_calls"] = tool_calls
    return mapped


def _map_tool_result_message(message: AgentMessage, mapped: JSONObject) -> JSONObject:
    if len(message.content) != 1 or not isinstance(
        message.content[0], ToolResultContent
    ):
        raise OpenAICompatibleMappingError(
            "Chat Completions tool messages require exactly one tool-result "
            "content part."
        )
    result = message.content[0]
    mapped["tool_call_id"] = result.tool_call_id
    mapped["content"] = _json_text(result.content)
    return mapped


def _map_tool_definition(tool: ToolDefinition) -> JSONObject:
    """Map a validated ToolDefinition without exposing provider-specific types."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _text_from_part(part: object) -> str:
    if isinstance(part, TextContent):
        return part.text
    raise OpenAICompatibleMappingError(
        "Only text content can be flattened into a prompt."
    )


def _json_text(value: JSONValue) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _as_object(value: object, description: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise OpenAICompatibleStreamError(f"{description} must be a JSON object.")
    return value


@dataclass
class _ToolCallState:
    tool_call_id: str | None = None
    name: str | None = None
    arguments: list[str] = field(default_factory=list)
    started: bool = False


class OpenAICompatibleStreamMapper:
    """Translate framed Chat Completions JSON payloads to normalized events.

    Only choice index zero is processed. Additional choices are deliberately
    ignored because the current agent contract represents one assistant reply.
    """

    def __init__(self, provider_id: str) -> None:
        """Create a response mapper with isolated tool-call state."""
        self._provider_id = provider_id
        self._started = False
        self._terminal = False
        self._tool_calls: dict[int, _ToolCallState] = {}

    def consume(self, payload: str) -> list[ModelStreamEvent]:
        """Map one complete SSE data payload into ordered normalized events."""
        if self._terminal:
            return []
        try:
            decoded: object = json.loads(payload)
        except json.JSONDecodeError as error:
            raise OpenAICompatibleStreamError(
                "Stream event contains malformed JSON."
            ) from error
        chunk = _as_object(decoded, "Stream event")
        events = self._start_event(chunk)
        events.extend(self._usage_events(chunk.get("usage")))
        choices = chunk.get("choices")
        if choices is None:
            return events
        if not isinstance(choices, list):
            raise OpenAICompatibleStreamError("Stream event choices must be an array.")
        for raw_choice in choices:
            choice = _as_object(raw_choice, "Stream choice")
            if choice.get("index", 0) != 0:
                continue
            events.extend(self._choice_events(choice))
        return events

    def finish(self) -> list[ModelStreamEvent]:
        """Finish an EOF or ``[DONE]`` stream with exactly one terminal event."""
        if self._terminal:
            return []
        events = self._start_event({})
        events.extend(self._complete(FinishReason.UNKNOWN, None))
        return events

    @property
    def is_terminal(self) -> bool:
        """Return whether a completion event has already been produced."""
        return self._terminal

    def _start_event(self, chunk: dict[str, object]) -> list[ModelStreamEvent]:
        if self._started:
            return []
        self._started = True
        request_id = chunk.get("id")
        model = chunk.get("model")
        return [
            StreamStarted(
                provider_request_id=request_id if isinstance(request_id, str) else None,
                model=model if isinstance(model, str) else None,
            )
        ]

    def _usage_events(self, value: object) -> list[ModelStreamEvent]:
        if value is None:
            return []
        usage = _as_object(value, "Stream usage")
        recognized = {"prompt_tokens", "completion_tokens", "total_tokens"}
        if not any(key in usage for key in recognized):
            return []
        prompt_details = usage.get("prompt_tokens_details")
        completion_details = usage.get("completion_tokens_details")
        cached = self._nested_non_negative_int(prompt_details, "cached_tokens")
        reasoning = self._nested_non_negative_int(
            completion_details, "reasoning_tokens"
        )
        try:
            normalized = UsageInfo(
                input_tokens=self._non_negative_int(usage.get("prompt_tokens")),
                output_tokens=self._non_negative_int(usage.get("completion_tokens")),
                cached_input_tokens=cached,
                reasoning_tokens=reasoning,
                total_tokens=self._non_negative_int(usage.get("total_tokens")),
            )
        except ValueError as error:
            raise OpenAICompatibleStreamError(
                "Stream usage values must be non-negative integers."
            ) from error
        return [UsageReported(usage=normalized)]

    def _choice_events(self, choice: dict[str, object]) -> list[ModelStreamEvent]:
        events: list[ModelStreamEvent] = []
        delta_value = choice.get("delta")
        if delta_value is not None:
            delta = _as_object(delta_value, "Stream choice delta")
            content = delta.get("content")
            if isinstance(content, str) and content:
                events.append(TextDelta(text=content))
            tool_calls = delta.get("tool_calls")
            if tool_calls is not None:
                events.extend(self._tool_events(tool_calls))
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None:
            if not isinstance(finish_reason, str):
                raise OpenAICompatibleStreamError(
                    "Stream finish_reason must be a string."
                )
            events.extend(
                self._complete(self._finish_reason(finish_reason), finish_reason)
            )
        return events

    def _tool_events(self, value: object) -> list[ModelStreamEvent]:
        if not isinstance(value, list):
            raise OpenAICompatibleStreamError("Stream tool_calls must be an array.")
        events: list[ModelStreamEvent] = []
        for raw_tool_call in value:
            tool_call = _as_object(raw_tool_call, "Stream tool call")
            index = tool_call.get("index")
            if not isinstance(index, int) or index < 0:
                raise OpenAICompatibleStreamError(
                    "Stream tool calls require a non-negative index."
                )
            state = self._tool_calls.setdefault(index, _ToolCallState())
            tool_call_id = tool_call.get("id")
            if tool_call_id is not None:
                if not isinstance(tool_call_id, str) or not tool_call_id:
                    raise OpenAICompatibleStreamError(
                        "Stream tool-call ID must be a non-empty string."
                    )
                if state.started and tool_call_id != state.tool_call_id:
                    raise OpenAICompatibleStreamError(
                        "Stream tool-call ID changed after start."
                    )
                state.tool_call_id = tool_call_id
            function_value = tool_call.get("function")
            if function_value is not None:
                function = _as_object(function_value, "Stream tool function")
                name = function.get("name")
                if name is not None:
                    if not isinstance(name, str) or not name:
                        raise OpenAICompatibleStreamError(
                            "Stream tool name must be a non-empty string."
                        )
                    if state.started and name != state.name:
                        raise OpenAICompatibleStreamError(
                            "Stream tool name changed after start."
                        )
                    state.name = name
                arguments = function.get("arguments")
                if arguments is not None:
                    if not isinstance(arguments, str):
                        raise OpenAICompatibleStreamError(
                            "Stream tool arguments must be text fragments."
                        )
                    state.arguments.append(arguments)
                    if state.started and state.tool_call_id is not None:
                        events.append(
                            ToolCallArgumentsDelta(
                                tool_call_id=state.tool_call_id,
                                arguments_delta=arguments,
                                index=index,
                            )
                        )
            events.extend(self._start_tool_call_if_ready(index, state))
        return events

    def _start_tool_call_if_ready(
        self, index: int, state: _ToolCallState
    ) -> list[ModelStreamEvent]:
        if state.started or state.tool_call_id is None or state.name is None:
            return []
        state.started = True
        events: list[ModelStreamEvent] = [
            ToolCallStarted(
                tool_call_id=state.tool_call_id, name=state.name, index=index
            )
        ]
        events.extend(
            ToolCallArgumentsDelta(
                tool_call_id=state.tool_call_id,
                arguments_delta=fragment,
                index=index,
            )
            for fragment in state.arguments
        )
        return events

    def _complete(
        self, finish_reason: FinishReason, provider_reason: str | None
    ) -> list[ModelStreamEvent]:
        if self._terminal:
            return []
        events: list[ModelStreamEvent] = []
        for index, state in sorted(self._tool_calls.items()):
            events.extend(self._start_tool_call_if_ready(index, state))
            if state.tool_call_id is None or state.name is None:
                raise OpenAICompatibleStreamError(
                    "Stream completed with a tool call missing an ID or name."
                )
            arguments_text = "".join(state.arguments)
            try:
                arguments: object = json.loads(arguments_text)
            except json.JSONDecodeError as error:
                raise OpenAICompatibleStreamError(
                    "Stream completed with malformed tool-call JSON arguments."
                ) from error
            if not isinstance(arguments, dict):
                raise OpenAICompatibleStreamError(
                    "Stream tool-call arguments must decode to a JSON object."
                )
            tool_call = ToolCall.model_validate(
                {"id": state.tool_call_id, "name": state.name, "arguments": arguments}
            )
            events.append(ToolCallCompleted(tool_call=tool_call))
        self._terminal = True
        events.append(
            StreamCompleted(
                finish_reason=finish_reason,
                provider_finish_reason=provider_reason,
            )
        )
        return events

    @staticmethod
    def _finish_reason(value: str) -> FinishReason:
        return {
            "stop": FinishReason.STOP,
            "length": FinishReason.LENGTH,
            "tool_calls": FinishReason.TOOL_CALLS,
            "content_filter": FinishReason.CONTENT_FILTER,
        }.get(value, FinishReason.UNKNOWN)

    @staticmethod
    def _non_negative_int(value: object) -> int:
        if value is None:
            return 0
        if not isinstance(value, int) or value < 0:
            raise ValueError("Expected a non-negative integer.")
        return value

    def _nested_non_negative_int(self, value: object, key: str) -> int:
        if value is None:
            return 0
        nested = _as_object(value, "Stream usage details")
        return self._non_negative_int(nested.get(key))
