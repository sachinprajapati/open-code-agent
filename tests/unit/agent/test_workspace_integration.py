"""Agent-loop integration with explicitly bound read-only workspace tools."""

from pathlib import Path

from open_code_agent.agent import (
    AgentRunner,
    AgentRunRequest,
    AgentRunStatus,
    collect_agent_run,
)
from open_code_agent.protocol import (
    FinishReason,
    ModelStreamEvent,
    StreamCompleted,
    StreamStarted,
    TextDelta,
    ToolCall,
    ToolCallCompleted,
    ToolCallStarted,
)
from open_code_agent.providers import MockModelProvider
from open_code_agent.tools import ListFilesTool, ReadFileTool, ToolRegistry
from open_code_agent.workspace import WorkspaceContext


def events_for_call(call: ToolCall) -> list[ModelStreamEvent]:
    """Build the normalized events needed for one complete mock tool call."""
    return [
        StreamStarted(),
        ToolCallStarted(tool_call_id=call.id, name=call.name),
        ToolCallCompleted(tool_call=call),
        StreamCompleted(finish_reason=FinishReason.TOOL_CALLS),
    ]


async def test_agent_inspection_flow_uses_only_bound_workspace(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("safe repository text\n")
    workspace = WorkspaceContext(root=tmp_path)
    registry = ToolRegistry()
    registry.register(ListFilesTool(workspace))
    registry.register(ReadFileTool(workspace))
    list_call = ToolCall(id="list", name="list_files", arguments={"max_results": 5})
    read_call = ToolCall(id="read", name="read_file", arguments={"path": "README.md"})
    provider = MockModelProvider(
        event_sequences=[
            events_for_call(list_call),
            events_for_call(read_call),
            [
                StreamStarted(),
                TextDelta(text="The README was inspected."),
                StreamCompleted(finish_reason=FinishReason.STOP),
            ],
        ]
    )

    result = await collect_agent_run(
        AgentRunner(provider, registry), AgentRunRequest(model="mock", task="inspect")
    )

    assert result.status is AgentRunStatus.COMPLETED
    assert result.total_tool_calls == 2
    assert [
        message.role.value for message in provider.received_requests[2].messages
    ] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    assert "safe repository text" in str(provider.received_requests[2])


async def test_agent_sensitive_path_attempt_never_returns_secret(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text("TOKEN=top-secret")
    registry = ToolRegistry()
    registry.register(ReadFileTool(WorkspaceContext(root=tmp_path)))
    call = ToolCall(id="secret", name="read_file", arguments={"path": ".env"})
    provider = MockModelProvider(
        event_sequences=[
            events_for_call(call),
            [
                StreamStarted(),
                TextDelta(text="I cannot access that file."),
                StreamCompleted(finish_reason=FinishReason.STOP),
            ],
        ]
    )

    result = await collect_agent_run(
        AgentRunner(provider, registry), AgentRunRequest(model="mock", task="read it")
    )
    assert result.status is AgentRunStatus.COMPLETED
    assert "top-secret" not in str(result)
    assert "top-secret" not in str(provider.received_requests)
