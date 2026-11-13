"""The explicit, sequential, provider-independent agent execution loop."""

import asyncio
from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import dataclass, field
from uuid import uuid4

from open_code_agent.agent.assembly import AgentStepResult, AssistantResponseAssembler
from open_code_agent.agent.events import (
    AgentRunEvent,
    ModelEventReceived,
    ModelStepCompleted,
    ModelStepStarted,
    RunCompleted,
    RunFailed,
    RunLimitReached,
    RunStarted,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from open_code_agent.agent.state import AgentRunRequest, AgentRunResult, AgentRunStatus
from open_code_agent.protocol import (
    AgentMessage,
    FinishReason,
    MessageRole,
    ModelError,
    ModelErrorCategory,
    ModelRequest,
    ToolCall,
    ToolResult,
    ToolResultContent,
    UsageInfo,
)
from open_code_agent.providers import ModelProvider
from open_code_agent.tools import (
    ToolArgumentValidationError,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolNotFoundError,
    ToolRegistry,
    validate_tool_arguments,
)


@dataclass
class _RunState:
    """Mutable state owned by exactly one stream invocation, never the runner."""

    run_id: str
    request: AgentRunRequest
    messages: list[AgentMessage]
    status: AgentRunStatus = AgentRunStatus.CREATED
    model_steps: int = 0
    total_tool_calls: int = 0
    usage: UsageInfo = field(default_factory=UsageInfo)
    final_text: str | None = None
    error: ModelError | None = None
    limit_reason: str | None = None


class AgentRunner:
    """Orchestrate a provider and explicitly registered local tools.

    Calls are intentionally sequential.  The runner stores no active-run state,
    does not execute provider-supplied code, and only exposes definitions from
    its ``ToolRegistry``.
    """

    def __init__(
        self, provider: ModelProvider, tools: ToolRegistry | None = None
    ) -> None:
        if not isinstance(provider, ModelProvider):
            raise TypeError("provider must satisfy the ModelProvider contract.")
        self._provider = provider
        self._tools = tools or ToolRegistry()

    def stream(self, request: AgentRunRequest) -> AsyncIterator[AgentRunEvent]:
        """Yield deterministic run events; cancellation propagates unchanged."""
        return self._stream(request)

    async def _stream(self, request: AgentRunRequest) -> AsyncIterator[AgentRunEvent]:
        messages = request.conversation()
        state = _RunState(
            run_id=str(uuid4()),
            request=request.model_copy(deep=True),
            messages=messages,
        )
        timeout_seconds = state.request.limits.overall_timeout_seconds
        try:
            if timeout_seconds is None:
                async for event in self._run(state):
                    yield event
            else:
                async with asyncio.timeout(timeout_seconds):
                    async for event in self._run(state):
                        yield event
        except TimeoutError:
            if not self._is_terminal(state):
                state.status = AgentRunStatus.LIMIT_REACHED
                state.limit_reason = "Overall timeout reached."
                yield RunLimitReached(run_id=state.run_id, result=self._snapshot(state))

    async def _run(self, state: _RunState) -> AsyncIterator[AgentRunEvent]:
        state.status = AgentRunStatus.RUNNING
        yield RunStarted(run_id=state.run_id)
        while True:
            if state.model_steps >= state.request.limits.max_model_steps:
                state.status = AgentRunStatus.LIMIT_REACHED
                state.limit_reason = "Maximum model steps reached."
                yield RunLimitReached(run_id=state.run_id, result=self._snapshot(state))
                return
            state.model_steps += 1
            step = state.model_steps
            state.status = AgentRunStatus.WAITING_FOR_MODEL
            yield ModelStepStarted(run_id=state.run_id, step=step)
            assembler = AssistantResponseAssembler()
            try:
                async for model_event in self._provider.stream(
                    self._model_request(state)
                ):
                    # Forward normalized events immediately for CLI/editor consumers.
                    yield ModelEventReceived(
                        run_id=state.run_id,
                        step=step,
                        model_event=model_event.model_copy(deep=True),
                    )
                    assembler.consume(model_event)
                step_result = assembler.result()
            except asyncio.CancelledError:
                raise
            except Exception:
                step_result = AgentStepResult(
                    error=self._local_model_error(
                        "Model stream could not be processed."
                    )
                )
            self._add_usage(state, step_result.usage)
            yield ModelStepCompleted(
                run_id=state.run_id,
                step=step,
                step_result=step_result.model_copy(deep=True),
            )
            if step_result.error is not None:
                state.status = AgentRunStatus.FAILED
                state.error = step_result.error.model_copy(deep=True)
                yield RunFailed(
                    run_id=state.run_id,
                    step=step,
                    error=state.error.model_copy(deep=True),
                    result=self._snapshot(state),
                )
                return
            if step_result.tool_calls:
                assert step_result.assistant_message is not None
                state.messages.append(
                    step_result.assistant_message.model_copy(deep=True)
                )
                state.status = AgentRunStatus.EXECUTING_TOOLS
                async for event in self._execute_tools(
                    state, step, step_result.tool_calls
                ):
                    yield event
                if state.status is AgentRunStatus.LIMIT_REACHED:
                    return
                continue
            if (
                step_result.finish_reason is FinishReason.TOOL_CALLS
                or step_result.assistant_message is None
                or not step_result.text
            ):
                state.status = AgentRunStatus.FAILED
                state.error = self._local_model_error(
                    "Model response did not contain a valid final assistant response."
                )
                yield RunFailed(
                    run_id=state.run_id,
                    step=step,
                    error=state.error.model_copy(deep=True),
                    result=self._snapshot(state),
                )
                return
            state.messages.append(step_result.assistant_message.model_copy(deep=True))
            state.final_text = step_result.text
            state.status = AgentRunStatus.COMPLETED
            yield RunCompleted(
                run_id=state.run_id, step=step, result=self._snapshot(state)
            )
            return

    def _model_request(self, state: _RunState) -> ModelRequest:
        """Construct one isolated streaming request from current run history."""
        return ModelRequest(
            model=state.request.model,
            messages=[message.model_copy(deep=True) for message in state.messages],
            tools=list(self._tools.definitions()),
            temperature=state.request.temperature,
            max_output_tokens=state.request.max_output_tokens,
            stop_sequences=list(state.request.stop_sequences),
            stream=True,
            metadata=(
                deepcopy(state.request.metadata)
                if state.request.metadata is not None
                else None
            ),
        )

    async def _execute_tools(
        self, state: _RunState, step: int, calls: list[ToolCall]
    ) -> AsyncIterator[AgentRunEvent]:
        for per_step_index, call in enumerate(calls):
            if per_step_index >= state.request.limits.max_tool_calls_per_step:
                state.status = AgentRunStatus.LIMIT_REACHED
                state.limit_reason = "Maximum tool calls per model step reached."
                yield RunLimitReached(
                    run_id=state.run_id, step=step, result=self._snapshot(state)
                )
                return
            if state.total_tool_calls >= state.request.limits.max_total_tool_calls:
                state.status = AgentRunStatus.LIMIT_REACHED
                state.limit_reason = "Maximum total tool calls reached."
                yield RunLimitReached(
                    run_id=state.run_id, step=step, result=self._snapshot(state)
                )
                return
            state.total_tool_calls += 1
            yield ToolExecutionStarted(
                run_id=state.run_id,
                step=step,
                tool_call_id=call.id,
                tool_name=call.name,
            )
            result = await self._execute_one(state, step, call)
            state.messages.append(
                AgentMessage(
                    role=MessageRole.TOOL,
                    content=[
                        ToolResultContent(
                            tool_call_id=call.id,
                            name=call.name,
                            content=result.content,
                            is_error=result.is_error,
                            metadata=result.metadata,
                        )
                    ],
                )
            )
            yield ToolExecutionCompleted(
                run_id=state.run_id,
                step=step,
                tool_call_id=call.id,
                tool_name=call.name,
                result=result.model_copy(deep=True),
            )

    async def _execute_one(
        self, state: _RunState, step: int, call: ToolCall
    ) -> ToolResult:
        try:
            tool = self._tools.get(call.name)
        except ToolNotFoundError:
            return self._tool_error(call, "The requested tool is unavailable.")
        try:
            validate_tool_arguments(tool.input_schema, call.arguments)
        except ToolArgumentValidationError:
            return self._tool_error(
                call, "Tool arguments did not match the input schema."
            )
        try:
            execution: object = await tool.execute(
                deepcopy(call.arguments),
                ToolExecutionContext(
                    run_id=state.run_id,
                    step=step,
                    tool_call_id=call.id,
                    metadata=(
                        deepcopy(state.request.metadata)
                        if state.request.metadata is not None
                        else None
                    ),
                ),
            )
            if not isinstance(execution, ToolExecutionResult):
                return self._tool_error(
                    call, "Tool returned an invalid execution result."
                )
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=execution.content,
                is_error=execution.is_error,
                metadata=(
                    deepcopy(execution.metadata)
                    if execution.metadata is not None
                    else None
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._tool_error(call, "Tool execution failed.")

    @staticmethod
    def _tool_error(call: ToolCall, message: str) -> ToolResult:
        return ToolResult(
            tool_call_id=call.id,
            name=call.name,
            content={"error": message},
            is_error=True,
        )

    @staticmethod
    def _local_model_error(message: str) -> ModelError:
        return ModelError(category=ModelErrorCategory.PROVIDER_ERROR, message=message)

    @staticmethod
    def _add_usage(state: _RunState, usage: UsageInfo | None) -> None:
        if usage is None:
            return
        state.usage = UsageInfo(
            input_tokens=state.usage.input_tokens + usage.input_tokens,
            output_tokens=state.usage.output_tokens + usage.output_tokens,
            cached_input_tokens=(
                state.usage.cached_input_tokens + usage.cached_input_tokens
            ),
            reasoning_tokens=state.usage.reasoning_tokens + usage.reasoning_tokens,
            total_tokens=state.usage.total_tokens + usage.total_tokens,
        )

    @staticmethod
    def _is_terminal(state: _RunState) -> bool:
        return state.status in {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
            AgentRunStatus.LIMIT_REACHED,
        }

    @staticmethod
    def _snapshot(state: _RunState) -> AgentRunResult:
        return AgentRunResult(
            run_id=state.run_id,
            status=state.status,
            model_steps=state.model_steps,
            total_tool_calls=state.total_tool_calls,
            messages=[message.model_copy(deep=True) for message in state.messages],
            usage=state.usage.model_copy(deep=True),
            final_text=state.final_text,
            error=state.error.model_copy(deep=True)
            if state.error is not None
            else None,
            limit_reason=state.limit_reason,
            metadata=(
                deepcopy(state.request.metadata)
                if state.request.metadata is not None
                else None
            ),
        )


async def collect_agent_run(
    runner: AgentRunner, request: AgentRunRequest
) -> AgentRunResult:
    """Consume one stream once and return its detached terminal result."""
    async for event in runner.stream(request):
        if isinstance(event, (RunCompleted, RunFailed, RunLimitReached)):
            return event.result.model_copy(deep=True)
    raise RuntimeError("Agent event stream ended without a terminal event.")
