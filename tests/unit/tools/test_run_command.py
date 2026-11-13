"""Approval binding and no-spawn behavior for the command tool."""

from pathlib import Path

from open_code_agent.execution import (
    ApprovalDecision,
    CommandExecutionPolicy,
    MockApprovalHandler,
)
from open_code_agent.protocol import JSONObject
from open_code_agent.tools import RunCommandTool, ToolExecutionContext
from open_code_agent.workspace import WorkspaceContext


def context() -> ToolExecutionContext:
    return ToolExecutionContext(run_id="run", step=1, tool_call_id="call")


async def test_blocked_command_never_prompts(tmp_path: Path) -> None:
    approvals = MockApprovalHandler(ApprovalDecision.APPROVED_ONCE)
    tool = RunCommandTool(
        WorkspaceContext(root=tmp_path),
        CommandExecutionPolicy(execution_enabled=True),
        approvals,
    )
    result = await tool.execute(
        {"executable": "rm", "arguments": ["x"], "reason": "bad"}, context()
    )
    assert result.is_error
    assert approvals.requests == []


async def test_denied_and_approved_command_behavior(tmp_path: Path) -> None:
    denied = MockApprovalHandler(ApprovalDecision.DENIED)
    tool = RunCommandTool(
        WorkspaceContext(root=tmp_path),
        CommandExecutionPolicy(execution_enabled=True),
        denied,
    )
    arguments: JSONObject = {
        "executable": "printf",
        "arguments": ["ok"],
        "reason": "check",
    }
    result = await tool.execute(arguments, context())
    assert result.is_error and len(denied.requests) == 1

    approved = MockApprovalHandler(ApprovalDecision.APPROVED_ONCE)
    executed = RunCommandTool(
        WorkspaceContext(root=tmp_path),
        CommandExecutionPolicy(execution_enabled=True),
        approved,
    )
    result = await executed.execute(arguments, context())
    assert not result.is_error
    assert isinstance(result.content, dict) and result.content["stdout"] == "ok"
