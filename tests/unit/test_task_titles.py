from pathlib import Path

from open_code_agent.persistence.tasks import TaskStore, task_title_from_prompt


def test_task_title_from_prompt_is_concise_and_deterministic() -> None:
    assert (
        task_title_from_prompt(
            "Create a file named checkpoint-test.txt containing exactly:\ncontents"
        )
        == "Create checkpoint-test.txt"
    )
    assert task_title_from_prompt(
        "Please, can you Fix the login form validation error?"
    ) == ("Fix login form validation")
    assert task_title_from_prompt("Explain how session persistence works") == (
        "Explain session persistence"
    )


def test_task_title_truncates_cleanly_at_sixty_characters() -> None:
    title = task_title_from_prompt(
        "Create a comprehensive integration test covering checkpoint "
        "restoration conflicts"
    )

    assert title.endswith("…")
    assert len(title) <= 60
    assert title == "Create a comprehensive integration test covering checkpoint…"


def test_first_user_prompt_updates_and_persists_placeholder_title(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    tasks = TaskStore(tmp_path / "data")
    task = tasks.create(root, title="New Task", profile_id=None, model=None, mode="ask")

    tasks.entry(
        task["task_id"],
        root,
        entry_id="run:user",
        kind="user",
        text="Fix the login form validation error",
    )

    assert tasks.get(task["task_id"], root)["title"] == "Fix login form validation"
    tasks.close()
    reopened = TaskStore(tmp_path / "data")
    assert reopened.get(task["task_id"], root)["title"] == "Fix login form validation"


def test_manual_title_is_not_overwritten_by_later_prompts(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    tasks = TaskStore(tmp_path / "data")
    task = tasks.create(root, title="New Task", profile_id=None, model=None, mode="ask")
    tasks.rename(task["task_id"], root, "My release checklist")

    tasks.entry(
        task["task_id"],
        root,
        entry_id="run:user",
        kind="user",
        text="Create a file named checkpoint-test.txt",
    )

    assert tasks.get(task["task_id"], root)["title"] == "My release checklist"


def test_old_placeholder_task_has_history_fallback_title(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    tasks = TaskStore(tmp_path / "data")
    task = tasks.create(root, title="New Task", profile_id=None, model=None, mode="ask")
    # Simulate an older persisted task created before automatic titles existed.
    tasks.db.execute(
        "INSERT INTO task_entries VALUES(?,?,?,?,?,?)",
        (
            task["task_id"],
            "old:user",
            "user",
            "Explain how session persistence works",
            "{}",
            "2026-01-01T00:00:00+00:00",
        ),
    )

    listed = tasks.list(root)

    assert listed[0]["title"] == "New Task"
    assert listed[0]["fallback_title"] == "Explain session persistence"
