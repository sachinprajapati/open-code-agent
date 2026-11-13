"""One-time approvals bound to immutable command fingerprints."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from open_code_agent.execution.models import CommandClassification, CommandProposal


class ApprovalDecision(StrEnum):
    APPROVED_ONCE = "approved_once"
    DENIED = "denied"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class CommandApprovalRequest:
    """Everything a human needs for one exact, review-only command approval."""

    proposal: CommandProposal
    classification: CommandClassification
    fingerprint: str


@runtime_checkable
class ApprovalHandler(Protocol):
    """Application-owned asynchronous human approval boundary."""

    async def request_approval(
        self, request: CommandApprovalRequest
    ) -> ApprovalDecision:
        """Return one terminal decision for this immutable request."""


class MockApprovalHandler:
    """Deterministic test handler that records requests without spawning commands."""

    def __init__(self, decision: ApprovalDecision = ApprovalDecision.DENIED) -> None:
        self.decision = decision
        self.requests: list[CommandApprovalRequest] = []
        self.raise_error = False

    async def request_approval(
        self, request: CommandApprovalRequest
    ) -> ApprovalDecision:
        self.requests.append(request)
        if self.raise_error:
            raise RuntimeError("approval failure")
        return self.decision
