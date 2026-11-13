"""Read-only file helpers shared by workspace tools."""

from pathlib import Path, PurePosixPath

from open_code_agent.workspace.context import WorkspaceContext
from open_code_agent.workspace.exceptions import SensitiveFileAccessError
from open_code_agent.workspace.paths import relative_workspace_path


def policy_allows_path(workspace: WorkspaceContext, path: Path) -> bool:
    """Return whether a resolved path is visible to tools under current policy."""
    relative = relative_workspace_path(workspace, path)
    policy = workspace.policy
    if policy.excludes_directory(relative):
        return False
    if (
        not policy.include_hidden_files
        and any(part.startswith(".") for part in relative.parts)
        and not (
            policy.allow_env_templates
            and relative.name in {".env.example", ".env.sample", ".env.template"}
        )
    ):
        return False
    return not policy.is_sensitive(relative)


def require_non_sensitive(workspace: WorkspaceContext, path: Path) -> PurePosixPath:
    """Return a relative path or fail before any content is opened."""
    relative = relative_workspace_path(workspace, path)
    if workspace.policy.is_sensitive(relative):
        raise SensitiveFileAccessError(
            "Access to this sensitive workspace file is denied."
        )
    return relative


def truncate_text(text: str, max_characters: int) -> tuple[str, bool]:
    """Truncate a Unicode string by characters without replacement decoding."""
    if len(text) <= max_characters:
        return text, False
    return text[:max_characters], True


def truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    """Bound model output bytes without splitting a UTF-8 code point."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True
