"""Security-focused workspace context, policy, and path tests."""

from pathlib import Path, PurePosixPath
from typing import cast

import pytest

from open_code_agent.protocol import JSONObject
from open_code_agent.workspace import (
    InvalidWorkspaceError,
    InvalidWorkspacePathError,
    WorkspaceAccessDeniedError,
    WorkspaceContext,
    WorkspacePolicy,
)
from open_code_agent.workspace.paths import resolve_workspace_path


def test_context_canonicalizes_and_isolates_metadata(tmp_path: Path) -> None:
    metadata = cast(JSONObject, {"nested": {"value": "original"}})
    context = WorkspaceContext(root=tmp_path, metadata=metadata)
    original_nested = metadata["nested"]
    assert isinstance(original_nested, dict)
    original_nested["value"] = "changed"
    snapshot = context.metadata
    assert context.root == tmp_path.resolve()
    assert snapshot == {"nested": {"value": "original"}}
    assert snapshot is not None
    nested = snapshot["nested"]
    assert isinstance(nested, dict)
    nested["value"] = "mutated"
    assert context.metadata == {"nested": {"value": "original"}}


def test_context_rejects_relative_missing_and_file_roots(tmp_path: Path) -> None:
    with pytest.raises(InvalidWorkspaceError):
        WorkspaceContext(root="relative")
    with pytest.raises(InvalidWorkspaceError):
        WorkspaceContext(root=tmp_path / "missing")
    file_path = tmp_path / "file.txt"
    file_path.write_text("x")
    with pytest.raises(InvalidWorkspaceError):
        WorkspaceContext(root=file_path)


def test_policy_sensitive_templates_and_limits() -> None:
    policy = WorkspacePolicy()
    assert policy.is_sensitive(PurePosixPath(".env"))
    assert policy.is_sensitive(PurePosixPath("private.pem"))
    assert not policy.is_sensitive(PurePosixPath(".env.example"))
    with pytest.raises(ValueError):
        WorkspacePolicy(max_read_lines=0)


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "nested/../../outside",
        "/etc/passwd",
        r"C:\\temp\\x",
        r"\\server\\share",
        r"..\\outside",
        "bad\x00path",
    ],
)
def test_path_rejects_traversal_and_windows_forms(tmp_path: Path, path: str) -> None:
    context = WorkspaceContext(root=tmp_path)
    with pytest.raises(InvalidWorkspacePathError):
        resolve_workspace_path(context, path)


def test_symlink_policy_blocks_escape_and_internal_link(tmp_path: Path) -> None:
    inside = tmp_path / "inside.txt"
    inside.write_text("inside")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside")
    (tmp_path / "inside-link").symlink_to(inside)
    (tmp_path / "outside-link").symlink_to(outside)

    denied = WorkspaceContext(root=tmp_path)
    with pytest.raises(WorkspaceAccessDeniedError):
        resolve_workspace_path(denied, "inside-link")
    with pytest.raises(WorkspaceAccessDeniedError):
        resolve_workspace_path(denied, "outside-link")

    allowed = WorkspaceContext(
        root=tmp_path, policy=WorkspacePolicy(follow_symlinks=True)
    )
    assert resolve_workspace_path(allowed, "inside-link") == inside.resolve()
    with pytest.raises(WorkspaceAccessDeniedError):
        resolve_workspace_path(allowed, "outside-link")
