from pathlib import Path

from open_code_agent.persistence.tasks import TaskStore
from open_code_agent.workspace import PatchProposalStore, WorkspaceContext


def _add_diff(path: str, content: str) -> str:
    return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+{content}\n"


def test_added_file_checkpoint_restores_only_matching_content(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    tasks = TaskStore(tmp_path / "data")
    task = tasks.create(root, title="test", profile_id=None, model=None, mode="ask")
    proposal_store = PatchProposalStore(WorkspaceContext(root=root))
    proposal = proposal_store.propose(_add_diff("new.txt", "created"))
    proposal_store.approve(proposal.proposal_id)
    proposal_store.apply_approved(proposal.proposal_id)
    checkpoint_id = tasks.record_patch_checkpoint(
        task["task_id"], root, proposal_store.get(proposal.proposal_id)
    )

    checkpoint = tasks.checkpoints(task["task_id"], root)[0]
    assert checkpoint["files"][0]["before"] is None
    assert checkpoint["files"][0]["after"]["content"] == "created\n"
    tasks.close()
    tasks = TaskStore(tmp_path / "data")
    persisted = tasks.checkpoints(task["task_id"], root)[0]
    assert persisted["checkpoint_id"] == checkpoint_id
    assert persisted["files"][0]["after"]["content"] == "created\n"
    assert (
        tasks.restore_checkpoint(task["task_id"], root, checkpoint_id)["restored"]
        is True
    )
    assert not (root / "new.txt").exists()

    proposal = proposal_store.propose(_add_diff("new.txt", "created"))
    proposal_store.approve(proposal.proposal_id)
    proposal_store.apply_approved(proposal.proposal_id)
    conflict_checkpoint = tasks.record_patch_checkpoint(
        task["task_id"], root, proposal_store.get(proposal.proposal_id)
    )
    (root / "new.txt").write_text("changed\n")
    result = tasks.restore_checkpoint(task["task_id"], root, conflict_checkpoint)
    assert result == {"restored": False, "conflicts": ["new.txt"]}
    assert (root / "new.txt").read_text() == "changed\n"


def test_restore_blocks_a_modified_file_that_changed_after_checkpoint(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "tracked.txt").write_text("before\n")
    tasks = TaskStore(tmp_path / "data")
    task = tasks.create(root, title="test", profile_id=None, model=None, mode="ask")
    proposals = PatchProposalStore(WorkspaceContext(root=root))
    proposal = proposals.propose(
        "--- a/tracked.txt\n+++ b/tracked.txt\n@@ -1 +1 @@\n-before\n+after\n"
    )
    proposals.approve(proposal.proposal_id)
    proposals.apply_approved(proposal.proposal_id)
    checkpoint_id = tasks.record_patch_checkpoint(
        task["task_id"], root, proposals.get(proposal.proposal_id)
    )
    (root / "tracked.txt").write_text("manual edit\n")

    assert tasks.restore_checkpoint(task["task_id"], root, checkpoint_id) == {
        "restored": False,
        "conflicts": ["tracked.txt"],
    }
    assert (root / "tracked.txt").read_text() == "manual edit\n"


def test_restore_accepts_later_agent_state_and_persists_restore_checkpoint(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    tasks = TaskStore(tmp_path / "data")
    task = tasks.create(root, title="test", profile_id=None, model=None, mode="ask")
    proposals = PatchProposalStore(WorkspaceContext(root=root))
    first = proposals.propose(_add_diff("checkpoint-test.txt", "checkpoint A"))
    proposals.approve(first.proposal_id)
    proposals.apply_approved(first.proposal_id)
    checkpoint_a = tasks.record_patch_checkpoint(
        task["task_id"], root, proposals.get(first.proposal_id)
    )
    second = proposals.propose(
        "--- a/checkpoint-test.txt\n+++ b/checkpoint-test.txt\n"
        "@@ -1 +1 @@\n-checkpoint A\n+checkpoint B\n"
    )
    proposals.approve(second.proposal_id)
    proposals.apply_approved(second.proposal_id)
    tasks.record_patch_checkpoint(task["task_id"], root, proposals.get(second.proposal_id))

    restored = tasks.restore_checkpoint(task["task_id"], root, checkpoint_a)
    assert restored["restored"] is True
    assert isinstance(restored["checkpoint_id"], str)
    assert not (root / "checkpoint-test.txt").exists()
    assert len(tasks.checkpoints(task["task_id"], root)) == 3

    (root / "checkpoint-test.txt").write_text("manual edit\n")
    assert tasks.restore_checkpoint(task["task_id"], root, checkpoint_a) == {
        "restored": False,
        "conflicts": ["checkpoint-test.txt"],
    }
    (root / "checkpoint-test.txt").write_text("checkpoint B\n")
    assert tasks.restore_checkpoint(task["task_id"], root, checkpoint_a)["restored"] is True
