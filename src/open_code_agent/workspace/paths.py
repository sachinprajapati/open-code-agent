"""Central containment and symlink checks for every workspace operation."""

from pathlib import Path, PurePosixPath, PureWindowsPath

from open_code_agent.workspace.context import WorkspaceContext
from open_code_agent.workspace.exceptions import (
    InvalidWorkspacePathError,
    WorkspaceAccessDeniedError,
    WorkspacePathNotFoundError,
)


def resolve_workspace_path(
    workspace: WorkspaceContext,
    raw_path: str,
    *,
    require_exists: bool = True,
    allow_root: bool = False,
) -> Path:
    """Resolve an untrusted relative path while enforcing symlink policy.

    This is not a complete defence against a malicious local process replacing
    filesystem objects between checks and reads.  It centralizes best-effort
    containment for normal repository use and never trusts string prefixes.
    """
    relative = normalize_workspace_path(raw_path, allow_root=allow_root)
    candidate = workspace.root.joinpath(*relative.parts)
    _check_symlink_components(workspace, relative)
    try:
        resolved = candidate.resolve(strict=require_exists)
    except FileNotFoundError as error:
        raise WorkspacePathNotFoundError("Workspace path does not exist.") from error
    except (OSError, RuntimeError) as error:
        raise WorkspaceAccessDeniedError(
            "Workspace path could not be resolved."
        ) from error
    if not _is_within(resolved, workspace.root):
        raise WorkspaceAccessDeniedError(
            "Workspace path resolves outside the workspace."
        )
    if require_exists and not resolved.exists():
        raise WorkspacePathNotFoundError("Workspace path does not exist.")
    return resolved


def normalize_workspace_path(
    raw_path: str, *, allow_root: bool = False
) -> PurePosixPath:
    """Validate a portable workspace-relative path without consulting CWD."""
    if not isinstance(raw_path, str) or "\x00" in raw_path:
        raise InvalidWorkspacePathError("Workspace path is invalid.")
    if not raw_path and not allow_root:
        raise InvalidWorkspacePathError("Workspace path must not be empty.")
    windows = PureWindowsPath(raw_path)
    if raw_path.startswith(("/", "\\")) or windows.is_absolute() or windows.drive:
        raise InvalidWorkspacePathError("Workspace path must be relative.")
    normalized = raw_path.replace("\\", "/")
    path = PurePosixPath(normalized)
    parts = tuple(part for part in path.parts if part != ".")
    if any(part == ".." for part in parts):
        raise InvalidWorkspacePathError("Workspace path traversal is not allowed.")
    if not parts:
        if allow_root:
            return PurePosixPath(".")
        raise InvalidWorkspacePathError("Workspace path must not be empty.")
    return PurePosixPath(*parts)


def relative_workspace_path(workspace: WorkspaceContext, path: Path) -> PurePosixPath:
    """Return a portable relative path only after proper containment checking."""
    try:
        return PurePosixPath(path.relative_to(workspace.root).as_posix())
    except ValueError as error:
        raise WorkspaceAccessDeniedError(
            "Workspace path resolves outside the workspace."
        ) from error


def _check_symlink_components(
    workspace: WorkspaceContext, relative: PurePosixPath
) -> None:
    current = workspace.root
    for part in relative.parts:
        current = current / part
        try:
            is_link = current.is_symlink()
        except OSError as error:
            raise WorkspaceAccessDeniedError(
                "Workspace path could not be inspected."
            ) from error
        if is_link and not workspace.policy.follow_symlinks:
            raise WorkspaceAccessDeniedError("Workspace symlinks are not allowed.")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
