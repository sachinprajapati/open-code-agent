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


def add_diff(path: str, content: str) -> str:
    lines = content.splitlines(keepends=True)
    return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n" + "".join(
        f"+{line}" for line in lines
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


def test_new_text_file_is_previewed_then_created_only_after_approval(
    tmp_path: Path,
) -> None:
    store = PatchProposalStore(WorkspaceContext(root=tmp_path))
    proposal = store.propose(
        add_diff("checkpoint-test.txt", "Phase 15 checkpoint test\n")
    )

    assert proposal.diff_metadata == (
        {
            "path": "checkpoint-test.txt",
            "status": "added",
            "content": "Phase 15 checkpoint test\n",
        },
    )
    assert not (tmp_path / "checkpoint-test.txt").exists()
    with pytest.raises(ProposalApprovalError):
        store.apply_approved(proposal.proposal_id)

    store.approve(proposal.proposal_id)
    assert store.apply_approved(proposal.proposal_id).complete
    assert (
        tmp_path / "checkpoint-test.txt"
    ).read_text() == "Phase 15 checkpoint test\n"


def test_new_file_patch_records_a_checkpoint_only_after_application(
    tmp_path: Path,
) -> None:
    recorded: list[str] = []

    class Recorder:
        def record(self, proposal: object) -> None:
            recorded.append(str(proposal))

    store = PatchProposalStore(WorkspaceContext(root=tmp_path), Recorder())
    proposal = store.propose(add_diff("created.txt", "created\n"))
    assert recorded == []
    store.approve(proposal.proposal_id)
    store.apply_approved(proposal.proposal_id)
    assert len(recorded) == 1


@pytest.mark.parametrize("path", ["../escape.txt", ".env", "existing.txt"])
def test_new_file_rejects_unsafe_or_existing_targets(tmp_path: Path, path: str) -> None:
    (tmp_path / "existing.txt").write_text("present\n")
    store = PatchProposalStore(WorkspaceContext(root=tmp_path))
    with pytest.raises(WorkspaceError):
        store.propose(add_diff(path, "new\n"))
