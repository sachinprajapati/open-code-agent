"""Approval-gated unified-diff proposal tests."""

from pathlib import Path

import pytest

from open_code_agent.workspace import (
    InvalidPatchError,
    PatchProposalStore,
    ProposalApprovalError,
    StaleProposalError,
    WorkspaceContext,
    WorkspaceError,
)


def diff(path: str, before: str, after: str) -> str:
    """Build a minimal valid one-line unified diff fixture."""
    return f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{before}\n+{after}\n"


def test_proposal_requires_approval_then_applies_exact_prepared_content(
    tmp_path: Path,
) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("before\n")
    store = PatchProposalStore(WorkspaceContext(root=tmp_path))
    proposal = store.propose(diff("notes.txt", "before", "after"))

    assert proposal.affected_paths == ("notes.txt",)
    assert target.read_text() == "before\n"
    with pytest.raises(ProposalApprovalError):
        store.apply_approved(proposal.proposal_id)

    store.approve(proposal.proposal_id)
    result = store.apply_approved(proposal.proposal_id)
    assert result.complete
    assert result.applied_paths == ("notes.txt",)
    assert target.read_text() == "after\n"


def test_stale_target_is_rejected_before_any_write(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one\n")
    second.write_text("two\n")
    proposal_text = diff("first.txt", "one", "ONE") + diff("second.txt", "two", "TWO")
    store = PatchProposalStore(WorkspaceContext(root=tmp_path))
    proposal = store.propose(proposal_text)
    store.approve(proposal.proposal_id)
    second.write_text("changed externally\n")

    with pytest.raises(StaleProposalError):
        store.apply_approved(proposal.proposal_id)
    assert first.read_text() == "one\n"
    assert second.read_text() == "changed externally\n"


def test_entire_proposal_is_rejected_when_one_target_is_forbidden(
    tmp_path: Path,
) -> None:
    safe = tmp_path / "safe.txt"
    secret = tmp_path / ".env"
    safe.write_text("safe\n")
    secret.write_text("TOKEN=secret\n")
    store = PatchProposalStore(WorkspaceContext(root=tmp_path))

    with pytest.raises(WorkspaceError) as error:
        store.propose(
            diff("safe.txt", "safe", "updated")
            + diff(".env", "TOKEN=secret", "TOKEN=changed")
        )
    assert "secret" not in str(error.value)
    assert safe.read_text() == "safe\n"
    assert secret.read_text() == "TOKEN=secret\n"


def test_malformed_patch_and_symlink_target_are_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("value\n")
    outside = tmp_path.parent / "outside-patch.txt"
    outside.write_text("outside\n")
    (tmp_path / "link.txt").symlink_to(outside)
    store = PatchProposalStore(WorkspaceContext(root=tmp_path))

    with pytest.raises(InvalidPatchError):
        store.propose("not a diff")
    with pytest.raises(WorkspaceError):
        store.propose(diff("link.txt", "outside", "changed"))
    assert outside.read_text() == "outside\n"
