"""Explicit, read-only workspace capability and policy APIs."""

from open_code_agent.workspace.context import WorkspaceContext
from open_code_agent.workspace.exceptions import (
    BinaryFileError,
    FileTooLargeError,
    InvalidPatchError,
    InvalidTextEncodingError,
    InvalidWorkspaceError,
    InvalidWorkspacePathError,
    PatchError,
    ProposalApprovalError,
    ProposalNotFoundError,
    SearchError,
    SearchTimeoutError,
    SensitiveFileAccessError,
    StaleProposalError,
    WorkspaceAccessDeniedError,
    WorkspaceError,
    WorkspacePathNotFoundError,
    WorkspacePathTypeError,
)
from open_code_agent.workspace.patches import (
    PatchApplyResult,
    PatchCheckpointRecorder,
    PatchProposal,
    PatchProposalStore,
)
from open_code_agent.workspace.policy import WorkspacePolicy

__all__ = [
    "BinaryFileError",
    "FileTooLargeError",
    "InvalidTextEncodingError",
    "InvalidPatchError",
    "PatchApplyResult",
    "PatchCheckpointRecorder",
    "PatchError",
    "PatchProposal",
    "PatchProposalStore",
    "ProposalApprovalError",
    "ProposalNotFoundError",
    "InvalidWorkspaceError",
    "InvalidWorkspacePathError",
    "SearchError",
    "SearchTimeoutError",
    "SensitiveFileAccessError",
    "WorkspaceAccessDeniedError",
    "WorkspaceContext",
    "WorkspaceError",
    "WorkspacePathNotFoundError",
    "WorkspacePathTypeError",
    "StaleProposalError",
    "WorkspacePolicy",
]
