"""Controlled environment and shell-free process runner tests."""

import asyncio
from pathlib import Path

import pytest

from open_code_agent.execution import (
    CommandClassifier,
    CommandExecutionPolicy,
    CommandProposal,
    ProcessRunner,
    build_child_environment,
)
from open_code_agent.workspace import WorkspaceContext


def make_proposal(executable: str, arguments: list[str]) -> CommandProposal:
    return CommandProposal.create(
        executable=executable,
        arguments=arguments,
        working_directory=".",
        timeout_seconds=1,
        reason="test process behavior",
    )


def test_controlled_environment_omits_secrets_and_protects_path() -> None:
    proposal = CommandProposal.create(
        executable="printf",
        arguments=["ok"],
        working_directory=".",
        timeout_seconds=1,
        reason="check environment",
        environment_additions={"SAFE_FLAG": "yes"},
    )
    policy = CommandExecutionPolicy(
        execution_enabled=True, allowed_environment_variables=frozenset({"SAFE_FLAG"})
    )
    policy.validate(proposal, CommandClassifier().classify(proposal))
    environment = build_child_environment(
        policy, proposal, {"PATH": "/bin", "API_TOKEN": "secret", "HOME": "/home/a"}
    )
    assert environment == {"PATH": "/bin", "HOME": "/home/a", "SAFE_FLAG": "yes"}


async def test_runner_captures_output_nonzero_and_literal_metacharacters(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceContext(root=tmp_path)
    policy = CommandExecutionPolicy(execution_enabled=True)
    runner = ProcessRunner(workspace, policy)
    proposal = make_proposal("printf", ["%s", "$(not-expanded)|*.txt"])
    result = await runner.run(proposal, CommandClassifier().classify(proposal))
    assert result.exit_code == 0
    assert result.stdout == "$(not-expanded)|*.txt"
    assert str(tmp_path) not in str(result.as_content())


async def test_runner_bounds_output_and_reaps_timeout(tmp_path: Path) -> None:
    workspace = WorkspaceContext(root=tmp_path)
    policy = CommandExecutionPolicy(
        execution_enabled=True,
        max_stdout_bytes=3,
        max_stderr_bytes=3,
        max_combined_output_bytes=4,
        max_line_length=2,
        termination_grace_seconds=0.1,
    )
    runner = ProcessRunner(workspace, policy)
    bounded = make_proposal("printf", ["abcdef\n"])
    result = await runner.run(bounded, CommandClassifier().classify(bounded))
    assert result.stdout_truncated
    timeout = make_proposal("sleep", ["2"])
    timeout = CommandProposal.create(
        executable=timeout.executable,
        arguments=timeout.arguments,
        working_directory=".",
        timeout_seconds=0.05,
        reason=timeout.reason,
    )
    timed = await runner.run(timeout, CommandClassifier().classify(timeout))
    assert timed.timed_out and timed.exit_code is None


async def test_runner_cancellation_reaps_child(tmp_path: Path) -> None:
    workspace = WorkspaceContext(root=tmp_path)
    runner = ProcessRunner(workspace, CommandExecutionPolicy(execution_enabled=True))
    command = make_proposal("sleep", ["2"])
    task = asyncio.create_task(
        runner.run(command, CommandClassifier().classify(command))
    )
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
