"""JSON-RPC backed adapters for the established one-time approval interfaces."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable

from open_code_agent.execution import ApprovalDecision, CommandApprovalRequest
from open_code_agent.protocol.common import JSONObject
from open_code_agent.tools import PatchApprovalRequest


class RpcApprovalHandler:
    """Request an exact approval from the client and fail closed on every error."""

    def __init__(
        self,
        request: Callable[[JSONObject], Awaitable[JSONObject | None]],
        context: Callable[[], tuple[str, str]],
    ) -> None:
        self._request = request
        self._context = context

    async def request_approval(
        self, request: CommandApprovalRequest
    ) -> ApprovalDecision:
        session_id, run_id = self._context()
        proposal = request.proposal
        result = await self._request(
            {
                "session_id": session_id,
                "run_id": run_id,
                "proposal_type": "command",
                "fingerprint": request.fingerprint,
                "proposal": {
                    "executable": proposal.executable,
                    "arguments": list(proposal.arguments),
                    "working_directory": proposal.working_directory.as_posix(),
                    "timeout_seconds": proposal.timeout_seconds,
                    "reason": proposal.reason,
                },
                "classification": {
                    "category": request.classification.category.value,
                    "risk": request.classification.risk.value,
                    "may_modify_files": request.classification.may_modify_files,
                    "may_use_network": request.classification.may_use_network,
                },
            }
        )
        if (
            result is None
            or result.get("approval_id") is not None
            and not isinstance(result["approval_id"], str)
        ):
            return ApprovalDecision.DENIED
        if result.get("fingerprint") != request.fingerprint:
            return ApprovalDecision.DENIED
        decision = result.get("decision")
        if not isinstance(decision, str):
            return ApprovalDecision.DENIED
        try:
            return ApprovalDecision(decision)
        except (TypeError, ValueError):
            return ApprovalDecision.DENIED

    async def request_patch_approval(self, request: PatchApprovalRequest) -> bool:
        session_id, run_id = self._context()
        proposal = request.proposal
        fingerprint = hashlib.sha256(proposal.diff.encode("utf-8")).hexdigest()
        result = await self._request(
            {
                "session_id": session_id,
                "run_id": run_id,
                "proposal_type": "patch",
                "fingerprint": fingerprint,
                "proposal": {
                    "proposal_id": proposal.proposal_id,
                    "affected_paths": list(proposal.affected_paths),
                    "diff": proposal.diff,
                },
            }
        )
        return bool(
            result is not None
            and result.get("fingerprint") == fingerprint
            and result.get("decision") == "approved_once"
        )
