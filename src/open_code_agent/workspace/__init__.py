"""Explicit, read-only workspace capability and policy APIs."""

from open_code_agent.workspace.context import WorkspaceContext
from open_code_agent.workspace.exceptions import (
    BinaryFileError,
    FileTooLargeError,
    InvalidTextEncodingError,
    InvalidWorkspaceError,
    InvalidWorkspacePathError,
    SearchError,
    SearchTimeoutError,
    SensitiveFileAccessError,
    WorkspaceAccessDeniedError,
    WorkspaceError,
    WorkspacePathNotFoundError,
    WorkspacePathTypeError,
)
from open_code_agent.workspace.policy import WorkspacePolicy

__all__ = [
    "BinaryFileError",
    "FileTooLargeError",
    "InvalidTextEncodingError",
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
    "WorkspacePolicy",
]
