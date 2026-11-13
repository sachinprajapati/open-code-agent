"""Model-facing patch proposal tool with an injected approval boundary."""

import asyncio
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from open_code_agent.protocol import JSONObject
from open_code_agent.tools.base import (
    ToolExecutionContext,
    ToolExecutionResult,
    ToolRisk,
)
from open_code_agent.workspace import PatchProposal, PatchProposalStore, WorkspaceError


@dataclass(frozen=True)
class PatchApprovalRequest:
    """The reviewable, immutable patch proposal presented to a human."""

    proposal: PatchProposal


@runtime_checkable
class PatchApprovalHandler(Protocol):
    """Application-owned one-time approval boundary for workspace edits."""

    async def request_patch_approval(self, request: PatchApprovalRequest) -> bool:
        """Return whether this exact proposal may be applied once."""


class ProposePatchTool:
    """Validate a patch and optionally apply it only after host approval."""

    name = "propose_patch"
    description = "Propose a bounded unified diff for explicit user approval."
    input_schema: JSONObject = {
        "type": "object",
        "required": ["diff"],
        "properties": {"diff": {"type": "string"}},
        "additionalProperties": False,
    }
    risk = ToolRisk.WRITE

    def __init__(
        self,
        proposals: PatchProposalStore,
        approvals: PatchApprovalHandler | None = None,
    ) -> None:
        self._proposals = proposals
        self._approvals = approvals

    async def execute(
        self, arguments: JSONObject, context: ToolExecutionContext
    ) -> ToolExecutionResult:
        """Store a validated proposal only; approval and writes stay in the CLI."""
        del context
        diff = arguments.get("diff")
        if not isinstance(diff, str):
            return ToolExecutionResult(
                content={"error": "Patch diff must be a string."}, is_error=True
            )
        try:
            proposal = self._proposals.propose(diff)
        except WorkspaceError as error:
            return ToolExecutionResult(
                content={
                    "error": {"code": type(error).__name__, "message": str(error)}
                },
                is_error=True,
            )
        content: JSONObject = {
            "proposal_id": proposal.proposal_id,
            "affected_paths": list(proposal.affected_paths),
            "status": proposal.status.value,
        }
        if self._approvals is None:
            return ToolExecutionResult(content=content)
        try:
            approved = await self._approvals.request_patch_approval(
                PatchApprovalRequest(proposal)
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return ToolExecutionResult(
                content={"error": "Patch approval failed."}, is_error=True
            )
        if not approved:
            return ToolExecutionResult(
                content={"error": "Patch approval was denied."}, is_error=True
            )
        try:
            result = self._proposals.apply_approved(
                self._proposals.approve(proposal.proposal_id).proposal_id
            )
        except WorkspaceError as error:
            return ToolExecutionResult(
                content={
                    "error": {"code": type(error).__name__, "message": str(error)}
                },
                is_error=True,
            )
        content["status"] = "applied" if result.complete else "partially_applied"
        content["applied_paths"] = list(result.applied_paths)
        if result.error is not None:
            content["error"] = result.error
        return ToolExecutionResult(content=content, is_error=not result.complete)
