"""Durable, secret-safe task history owned by the Python application core."""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from open_code_agent.workspace import PatchProposal
from open_code_agent.workspace.exceptions import WorkspaceError
from open_code_agent.workspace.paths import resolve_workspace_path


def _now() -> str:
    return datetime.now(UTC).isoformat()


def task_title_from_prompt(prompt: str) -> str:
    """Make a stable, local task title from the first user prompt."""
    text = " ".join(prompt.split()).strip(" \t\r\n\"'“”‘’")
    text = re.sub(
        r"^(?:please[\s,]+)?(?:can|could|would)\s+you\s+",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"^please[\s,]+", "", text, flags=re.I)
    text = re.split(r"[!?;:]|\.(?=\s|$)", text, maxsplit=1)[0].strip()
    text = re.sub(r"^(create)\s+(?:a|an)\s+file\s+named\s+", r"\1 ", text, flags=re.I)
    text = re.sub(r"\s+containing\s+(?:exactly|the following).*$", "", text, flags=re.I)
    text = re.sub(r"^(fix)\s+the\s+", r"\1 ", text, flags=re.I)
    text = re.sub(r"\s+error$", "", text, flags=re.I)
    text = re.sub(r"^(explain)\s+how\s+", r"\1 ", text, flags=re.I)
    text = re.sub(r"\s+works$", "", text, flags=re.I)
    text = text.strip(" \t\r\n\"'“”‘’") or "New Task"
    if len(text) <= 60:
        return text
    words: list[str] = []
    for word in text.split():
        candidate = " ".join([*words, word])
        if len(candidate) + 1 > 60:
            break
        words.append(word)
    return f"{' '.join(words) or text[:59].rstrip()}…"


def _is_placeholder_title(title: str) -> bool:
    return title.casefold() == "new task"


def workspace_identity(path: Path) -> tuple[str, str]:
    """Return an identity from a resolved local workspace, never UI metadata."""
    resolved = path.resolve(strict=True)
    return hashlib.sha256(str(resolved).encode("utf-8")).hexdigest(), str(resolved)


class TaskStore:
    """Small SQLite store with bounded transcripts and transactional writes.

    Content is deliberately limited to user-visible messages and already-redacted
    event summaries. Provider credentials and environment values have no column.
    """

    MAX_MESSAGES = 2_000
    MAX_MESSAGE_BYTES = 128_000
    MAX_EVENT_BYTES = 32_000

    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "tasks.sqlite3"
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)"
        )
        if not self.db.execute(
            "SELECT 1 FROM schema_migrations WHERE version=1"
        ).fetchone():
            self.db.executescript("""
        CREATE TABLE tasks (
          task_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, workspace_path TEXT NOT NULL,
          title TEXT NOT NULL, profile_id TEXT, model TEXT, mode TEXT NOT NULL,
          state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          usage_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX tasks_workspace_updated ON tasks(workspace_id, updated_at DESC);
        CREATE TABLE task_entries (
          task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
          entry_id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
          PRIMARY KEY(task_id, entry_id)
        );
        CREATE TABLE checkpoints (
          checkpoint_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
          source_proposal_id TEXT, created_at TEXT NOT NULL, files_json TEXT NOT NULL
        );
        INSERT INTO schema_migrations(version) VALUES(1);
        """)
        if not self.db.execute(
            "SELECT 1 FROM schema_migrations WHERE version=2"
        ).fetchone():
            self.db.execute(
                "ALTER TABLE tasks ADD COLUMN title_is_manual INTEGER NOT NULL DEFAULT 0"
            )
            self.db.execute("INSERT INTO schema_migrations(version) VALUES(2)")

    def close(self) -> None:
        self.db.close()

    def create(
        self,
        root: Path,
        *,
        title: str,
        profile_id: str | None,
        model: str | None,
        mode: str,
    ) -> dict[str, object]:
        wid, path = workspace_identity(root)
        task_id, stamp = str(uuid4()), _now()
        with self.db:
            self.db.execute(
                "INSERT INTO tasks(task_id,workspace_id,workspace_path,title,profile_id,model,mode,state,created_at,updated_at,usage_json,title_is_manual) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    wid,
                    path,
                    title[:200] or "New Task",
                    profile_id,
                    model,
                    mode,
                    "active",
                    stamp,
                    stamp,
                    "{}",
                    0,
                ),
            )
        return self.get(task_id, root)

    def get(self, task_id: str, root: Path) -> dict[str, object]:
        wid, _ = workspace_identity(root)
        row = self.db.execute(
            "SELECT * FROM tasks WHERE task_id=? AND workspace_id=?", (task_id, wid)
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        result = dict(row)
        result["usage"] = json.loads(result.pop("usage_json"))
        result["entries"] = [
            dict(item)
            for item in self.db.execute(
                "SELECT entry_id,kind,text,payload_json,created_at FROM task_entries WHERE task_id=? ORDER BY created_at,entry_id",
                (task_id,),
            )
        ]
        for item in result["entries"]:
            item["payload"] = json.loads(item.pop("payload_json"))
        result["checkpoints"] = self.checkpoints(task_id, root)
        return result

    def checkpoints(self, task_id: str, root: Path) -> list[dict[str, object]]:
        """Return durable checkpoint metadata for one workspace-bound task."""
        self._require_task(task_id, root)
        rows = self.db.execute(
            "SELECT checkpoint_id,source_proposal_id,created_at,files_json "
            "FROM checkpoints WHERE task_id=? ORDER BY created_at,checkpoint_id",
            (task_id,),
        ).fetchall()
        return [
            {
                "checkpoint_id": row["checkpoint_id"],
                "source_proposal_id": row["source_proposal_id"],
                "created_at": row["created_at"],
                "files": json.loads(row["files_json"]),
            }
            for row in rows
        ]

    def record_patch_checkpoint(
        self, task_id: str, root: Path, proposal: PatchProposal
    ) -> str:
        """Persist an approved patch's exact before/after bytes and hashes."""
        self._require_task(task_id, root)
        files = [
            {
                "path": change.path.as_posix(),
                "before": None
                if change.original_bytes is None
                else {
                    "hash": change.original_hash,
                    "content": change.original_bytes.decode("utf-8-sig"),
                },
                "after": {
                    "hash": hashlib.sha256(change.new_bytes).hexdigest(),
                    "content": change.new_bytes.decode("utf-8-sig"),
                },
            }
            for change in proposal.changes
        ]
        checkpoint_id = str(uuid4())
        with self.db:
            self.db.execute(
                "INSERT INTO checkpoints VALUES(?,?,?,?,?)",
                (
                    checkpoint_id,
                    task_id,
                    proposal.proposal_id,
                    _now(),
                    json.dumps(files),
                ),
            )
        return checkpoint_id

    def restore_checkpoint(
        self, task_id: str, root: Path, checkpoint_id: str
    ) -> dict[str, object]:
        """Restore a lineage state, refusing bytes outside agent-managed history."""
        self._require_task(task_id, root)
        row = self.db.execute(
            "SELECT files_json FROM checkpoints WHERE checkpoint_id=? AND task_id=?",
            (checkpoint_id, task_id),
        ).fetchone()
        if row is None:
            raise KeyError(checkpoint_id)
        files = json.loads(row["files_json"])
        lineage = self._lineage_states(task_id)
        conflicts: list[str] = []
        removals: list[Path] = []
        replacements: list[tuple[Path, str]] = []
        checkpoint_files: list[dict[str, object]] = []
        from open_code_agent.workspace import WorkspaceContext

        workspace = WorkspaceContext(root=root)
        for file in files:
            try:
                path = resolve_workspace_path(
                    workspace, file["path"], require_exists=False
                )
            except WorkspaceError:
                conflicts.append(file["path"])
                continue
            current = self._file_state(path)
            current_hash = None if current is None else current["hash"]
            if current_hash not in lineage.get(file["path"], set()):
                conflicts.append(file["path"])
                continue
            target = file.get("before")
            checkpoint_files.append(
                {"path": file["path"], "before": current, "after": target}
            )
            if target is None:
                if current is not None:
                    removals.append(path)
            else:
                replacements.append((path, target["content"]))
        if conflicts:
            return {"restored": False, "conflicts": conflicts}
        for path in removals:
            path.unlink()
        for path, content in replacements:
            path.write_text(content, encoding="utf-8")
        restore_checkpoint_id = self._record_checkpoint(
            task_id, f"restore:{checkpoint_id}", checkpoint_files
        )
        return {
            "restored": True,
            "conflicts": [],
            "checkpoint_id": restore_checkpoint_id,
            "removed_paths": [
                str(path.relative_to(root)).replace("\\", "/") for path in removals
            ],
            "restored_paths": [
                str(path.relative_to(root)).replace("\\", "/")
                for path, _ in replacements
            ],
        }

    def restore_preview(
        self, task_id: str, root: Path, checkpoint_id: str
    ) -> list[dict[str, object]]:
        """Return current and target states for an explicit restore review."""
        self._require_task(task_id, root)
        row = self.db.execute(
            "SELECT files_json FROM checkpoints WHERE checkpoint_id=? AND task_id=?",
            (checkpoint_id, task_id),
        ).fetchone()
        if row is None:
            raise KeyError(checkpoint_id)
        from open_code_agent.workspace import WorkspaceContext

        workspace = WorkspaceContext(root=root)
        result: list[dict[str, object]] = []
        for file in json.loads(row["files_json"]):
            path = resolve_workspace_path(workspace, file["path"], require_exists=False)
            result.append(
                {
                    "path": file["path"],
                    "current": self._file_state(path),
                    "target": file.get("before"),
                }
            )
        return result

    def _lineage_states(self, task_id: str) -> dict[str, set[str | None]]:
        states: dict[str, set[str | None]] = {}
        rows = self.db.execute(
            "SELECT files_json FROM checkpoints WHERE task_id=?", (task_id,)
        ).fetchall()
        for row in rows:
            for file in json.loads(row["files_json"]):
                path_states = states.setdefault(file["path"], set())
                for state in (file.get("before"), file.get("after")):
                    path_states.add(None if state is None else state["hash"])
        return states

    def _file_state(self, path: Path) -> dict[str, str] | None:
        if not path.is_file():
            return None
        content = path.read_bytes()
        return {
            "hash": hashlib.sha256(content).hexdigest(),
            "content": content.decode("utf-8-sig"),
        }

    def _record_checkpoint(
        self, task_id: str, source: str, files: list[dict[str, object]]
    ) -> str:
        checkpoint_id = str(uuid4())
        with self.db:
            self.db.execute(
                "INSERT INTO checkpoints VALUES(?,?,?,?,?)",
                (checkpoint_id, task_id, source, _now(), json.dumps(files)),
            )
        return checkpoint_id

    def _require_task(self, task_id: str, root: Path) -> None:
        wid, _ = workspace_identity(root)
        exists = self.db.execute(
            "SELECT 1 FROM tasks WHERE task_id=? AND workspace_id=?", (task_id, wid)
        ).fetchone()
        if exists is None:
            raise KeyError(task_id)

    def list(self, root: Path, query: str = "") -> list[dict[str, object]]:
        wid, _ = workspace_identity(root)
        like = f"%{query.strip()}%"
        rows = self.db.execute(
            "SELECT tasks.*, (SELECT text FROM task_entries "
            "WHERE task_id=tasks.task_id AND kind='user' "
            "ORDER BY created_at,entry_id LIMIT 1) AS first_user_message "
            "FROM tasks WHERE workspace_id=? AND (title LIKE ? OR task_id LIKE ?) "
            "ORDER BY updated_at DESC",
            (wid, like, like),
        ).fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            item = dict(row)
            first_user = item.pop("first_user_message")
            item["usage"] = json.loads(item.pop("usage_json"))
            if _is_placeholder_title(item["title"]) and isinstance(first_user, str):
                item["fallback_title"] = task_title_from_prompt(first_user)
            result.append(item)
        return result

    def rename(self, task_id: str, root: Path, title: str) -> None:
        if not title.strip() or len(title) > 200:
            raise ValueError("Task title must be between 1 and 200 characters.")
        wid, _ = workspace_identity(root)
        with self.db:
            changed = self.db.execute(
                "UPDATE tasks SET title=?,title_is_manual=1,updated_at=? WHERE task_id=? AND workspace_id=?",
                (title.strip(), _now(), task_id, wid),
            ).rowcount
        if not changed:
            raise KeyError(task_id)

    def delete(self, task_id: str, root: Path) -> None:
        wid, _ = workspace_identity(root)
        with self.db:
            changed = self.db.execute(
                "DELETE FROM tasks WHERE task_id=? AND workspace_id=?", (task_id, wid)
            ).rowcount
        if not changed:
            raise KeyError(task_id)

    def clear(self, root: Path) -> int:
        wid, _ = workspace_identity(root)
        with self.db:
            return self.db.execute(
                "DELETE FROM tasks WHERE workspace_id=?", (wid,)
            ).rowcount

    def entry(
        self,
        task_id: str,
        root: Path,
        *,
        entry_id: str,
        kind: str,
        text: str = "",
        payload: dict[str, object] | None = None,
    ) -> None:
        # Do not accept unbounded streamed payloads; callers use stable ids for idempotency.
        text = text.encode("utf-8")[: self.MAX_MESSAGE_BYTES].decode("utf-8", "ignore")
        encoded = json.dumps(payload or {}, ensure_ascii=False)[: self.MAX_EVENT_BYTES]
        wid, _ = workspace_identity(root)
        with self.db:
            exists = self.db.execute(
                "SELECT 1 FROM tasks WHERE task_id=? AND workspace_id=?", (task_id, wid)
            ).fetchone()
            if exists is None:
                raise KeyError(task_id)
            count = self.db.execute(
                "SELECT count(*) FROM task_entries WHERE task_id=?", (task_id,)
            ).fetchone()[0]
            if count >= self.MAX_MESSAGES:
                raise ValueError("Task transcript limit reached; start a new task.")
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO task_entries VALUES(?,?,?,?,?,?)",
                (task_id, entry_id, kind, text, encoded, _now()),
            ).rowcount
            if kind == "user" and inserted:
                task = self.db.execute(
                    "SELECT title,title_is_manual FROM tasks WHERE task_id=? AND workspace_id=?",
                    (task_id, wid),
                ).fetchone()
                if (
                    task is not None
                    and not task["title_is_manual"]
                    and _is_placeholder_title(task["title"])
                ):
                    self.db.execute(
                        "UPDATE tasks SET title=?,updated_at=? WHERE task_id=? AND workspace_id=?",
                        (task_title_from_prompt(text), _now(), task_id, wid),
                    )
            self.db.execute(
                "UPDATE tasks SET updated_at=? WHERE task_id=?", (_now(), task_id)
            )

    def state(
        self,
        task_id: str,
        root: Path,
        state: str,
        usage: dict[str, object] | None = None,
    ) -> None:
        if state not in {"active", "completed", "cancelled", "failed"}:
            raise ValueError("Invalid task state.")
        wid, _ = workspace_identity(root)
        with self.db:
            changed = self.db.execute(
                "UPDATE tasks SET state=?,usage_json=?,updated_at=? WHERE task_id=? AND workspace_id=?",
                (state, json.dumps(usage or {}), _now(), task_id, wid),
            ).rowcount
        if not changed:
            raise KeyError(task_id)
