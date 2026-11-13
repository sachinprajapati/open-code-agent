"""Approval-gated structured development command tool."""

import asyncio

from open_code_agent.execution import (
    ApprovalDecision,
    ApprovalHandler,
    CommandApprovalRequest,
    CommandClassifier,
    CommandExecutionPolicy,
    CommandProposal,
    ProcessRunner,
)
from open_code_agent.protocol import JSONObject
from open_code_agent.tools.base import (
    ToolExecutionContext,
    ToolExecutionResult,
    ToolRisk,
)
from open_code_agent.workspace import WorkspaceContext


class RunCommandTool:
    """Run one classified argv proposal after an injected human approval."""

    name = "run_command"
    description = "Propose an approved, bounded development command without a shell."
    input_schema: JSONObject = {
        "type": "object",
        "required": ["executable", "reason"],
        "properties": {
            "executable": {"type": "string"},
            "arguments": {"type": "array"},
            "working_directory": {"type": "string"},
            "timeout_seconds": {"type": "number"},
            "reason": {"type": "string"},
        },
        "additionalProperties": False,
    }
    risk = ToolRisk.EXECUTE

    def __init__(
        self,
        workspace: WorkspaceContext,
        policy: CommandExecutionPolicy | None,
        approvals: ApprovalHandler | None,
        runner: ProcessRunner | None = None,
        classifier: CommandClassifier | None = None,
    ) -> None:
        self._workspace = workspace
        self._policy = policy
        self._approvals = approvals
        self._classifier = classifier or CommandClassifier()
        self._runner = runner

    async def execute(
        self, arguments: JSONObject, context: ToolExecutionContext
    ) -> ToolExecutionResult:
        """Validate, classify, approve, bind, then create a process exactly once."""
        del context
        if self._policy is None or self._approvals is None:
            return _error("Command execution is unavailable without approval policy.")
        try:
            proposal = CommandProposal.create(
                executable=arguments.get("executable"),
                arguments=arguments.get("arguments", []),
                working_directory=arguments.get("working_directory", "."),
                timeout_seconds=arguments.get("timeout_seconds", 120.0),
                reason=arguments.get("reason"),
            )
            classification = self._classifier.classify(proposal)
            self._policy.validate(proposal, classification)
        except Exception as error:
            return _error(str(error))
        request = CommandApprovalRequest(
            proposal=proposal,
            classification=classification,
            fingerprint=proposal.fingerprint,
        )
        try:
            decision = await self._approvals.request_approval(request)
        except asyncio.CancelledError:
            raise
        except Exception:
            return _error("Command approval failed.")
        if decision is ApprovalDecision.CANCELLED:
            return _error("Command approval was cancelled.")
        if decision is not ApprovalDecision.APPROVED_ONCE:
            return _error("Command approval was denied.")
        if request.fingerprint != proposal.fingerprint:
            return _error("Command proposal changed after approval.")
        try:
            runner = self._runner or ProcessRunner(self._workspace, self._policy)
            result = await runner.run(proposal, classification)
        except asyncio.CancelledError:
            raise
        except Exception:
            return _error("Command could not be executed safely.")
        return ToolExecutionResult(content=result.as_content(), is_error=False)


def _error(message: str) -> ToolExecutionResult:
    return ToolExecutionResult(content={"error": message}, is_error=True)
