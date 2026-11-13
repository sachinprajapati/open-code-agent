"""Expected local errors at the read-only workspace boundary."""

from open_code_agent.exceptions import OpenCodeAgentError


class WorkspaceError(OpenCodeAgentError):
    """Base exception for safe workspace operations."""


class InvalidWorkspaceError(WorkspaceError):
    """Raised when a configured workspace root is unusable."""


class InvalidWorkspacePathError(WorkspaceError):
    """Raised for malformed or traversal-capable workspace-relative paths."""


class WorkspaceAccessDeniedError(WorkspaceError):
    """Raised when a path violates workspace containment or policy."""


class WorkspacePathNotFoundError(WorkspaceError):
    """Raised when an otherwise valid workspace-relative path is absent."""


class WorkspacePathTypeError(WorkspaceError):
    """Raised when an operation is requested on the wrong filesystem type."""


class SensitiveFileAccessError(WorkspaceAccessDeniedError):
    """Raised without reading a path covered by the sensitive-file policy."""


class FileTooLargeError(WorkspaceError):
    """Raised before a configured read-size bound is exceeded."""


class BinaryFileError(WorkspaceError):
    """Raised when a file contains NUL bytes and is treated as binary."""


class InvalidTextEncodingError(WorkspaceError):
    """Raised when a text file is not valid UTF-8 or UTF-8 with BOM."""


class SearchError(WorkspaceError):
    """Raised for a safe, normalized ripgrep failure."""


class SearchTimeoutError(SearchError):
    """Raised when ripgrep exceeds the workspace search timeout."""


class PatchError(WorkspaceError):
    """Base exception for safe patch proposal validation and application."""


class InvalidPatchError(PatchError):
    """Raised for malformed, over-limit, or unsupported unified diffs."""


class ProposalNotFoundError(PatchError):
    """Raised for an unknown proposal identifier."""


class ProposalApprovalError(PatchError):
    """Raised when an unapproved or already applied proposal is applied."""


class StaleProposalError(PatchError):
    """Raised when a target changed after proposal validation or preview."""
