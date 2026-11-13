"""Async shell-free process execution with bounded output and cleanup."""

import asyncio
import contextlib
import os
import signal
import time

from open_code_agent.execution.models import (
    CommandClassification,
    CommandExecutionResult,
    CommandProposal,
)
from open_code_agent.execution.output import BoundedOutput, CombinedOutputBudget
from open_code_agent.execution.policy import (
    CommandExecutionPolicy,
    build_child_environment,
)
from open_code_agent.workspace import WorkspaceContext
from open_code_agent.workspace.paths import resolve_workspace_path


class ProcessRunner:
    """Run approved argv inside one workspace; no shell is ever involved.

    POSIX executions start a new session so timeout/cancellation can terminate a
    process group. Other platforms terminate the direct child as best effort.
    This is not container or OS sandbox isolation.
    """

    def __init__(
        self, workspace: WorkspaceContext, policy: CommandExecutionPolicy
    ) -> None:
        self._workspace = workspace
        self._policy = policy

    async def run(
        self, proposal: CommandProposal, classification: CommandClassification
    ) -> CommandExecutionResult:
        """Spawn only a prevalidated proposal and always reap it."""
        self._policy.validate(proposal, classification)
        working_directory = resolve_workspace_path(
            self._workspace, proposal.working_directory.as_posix(), allow_root=True
        )
        if not working_directory.is_dir():
            raise ValueError("Command working directory must be a directory.")
        environment = build_child_environment(self._policy, proposal)
        started_at = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            proposal.executable,
            *proposal.arguments,
            cwd=str(working_directory),
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
        combined = CombinedOutputBudget(self._policy.max_combined_output_bytes)
        stdout = BoundedOutput(
            self._policy.max_stdout_bytes, self._policy.max_line_length, combined
        )
        stderr = BoundedOutput(
            self._policy.max_stderr_bytes, self._policy.max_line_length, combined
        )
        readers = [
            asyncio.create_task(_drain(process.stdout, stdout)),
            asyncio.create_task(_drain(process.stderr, stderr)),
        ]
        timed_out = False
        exit_code: int | None = None
        try:
            async with asyncio.timeout(proposal.timeout_seconds):
                await process.wait()
            exit_code = process.returncode
        except TimeoutError:
            timed_out = True
            await self._terminate_and_reap(process)
        except asyncio.CancelledError:
            await self._terminate_and_reap(process)
            raise
        finally:
            await asyncio.gather(*readers, return_exceptions=True)
        return CommandExecutionResult(
            proposal_fingerprint=proposal.fingerprint,
            executable=proposal.executable,
            arguments=proposal.arguments,
            working_directory=proposal.working_directory.as_posix(),
            classification=classification.category,
            risk=classification.risk,
            exit_code=exit_code,
            stdout=stdout.text,
            stderr=stderr.text,
            stdout_truncated=stdout.truncated,
            stderr_truncated=stderr.truncated,
            combined_output_truncated=combined.truncated,
            timed_out=timed_out,
            duration_seconds=round(time.monotonic() - started_at, 6),
            approved=True,
            started=True,
        )

    async def _terminate_and_reap(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if os.name == "posix":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        try:
            async with asyncio.timeout(self._policy.termination_grace_seconds):
                await process.wait()
                return
        except TimeoutError:
            pass
        if os.name == "posix":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        await process.wait()


async def _drain(stream: asyncio.StreamReader | None, output: BoundedOutput) -> None:
    if stream is None:
        return
    while chunk := await stream.read(4_096):
        output.feed(chunk)
