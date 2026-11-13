"""Read-only, policy-bound recursive workspace listing tool."""

from pathlib import Path, PurePosixPath
from typing import cast

from open_code_agent.protocol import JSONObject, JSONValue
from open_code_agent.tools.base import (
    ToolExecutionContext,
    ToolExecutionResult,
    ToolRisk,
)
from open_code_agent.workspace import (
    WorkspaceAccessDeniedError,
    WorkspaceContext,
    WorkspaceError,
    WorkspacePathTypeError,
)
from open_code_agent.workspace.files import policy_allows_path
from open_code_agent.workspace.paths import (
    normalize_workspace_path,
    relative_workspace_path,
    resolve_workspace_path,
)


class ListFilesTool:
    """List visible files and directories under one explicitly bound workspace."""

    name = "list_files"
    description = (
        "List visible workspace files and directories without reading content."
    )
    input_schema: JSONObject = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "pattern": {"type": "string"},
            "recursive": {"type": "boolean"},
            "max_results": {"type": "integer"},
        },
        "additionalProperties": False,
    }
    risk = ToolRisk.READ

    def __init__(self, workspace: WorkspaceContext) -> None:
        self._workspace = workspace

    async def execute(
        self, arguments: JSONObject, context: ToolExecutionContext
    ) -> ToolExecutionResult:
        """Return deterministic relative entries; expected failures are data."""
        del context
        try:
            return self._list(arguments)
        except WorkspaceError as error:
            return _workspace_error(error)

    def _list(self, arguments: JSONObject) -> ToolExecutionResult:
        raw_path = _optional_string(arguments, "path", ".")
        assert raw_path is not None
        pattern = _optional_string(arguments, "pattern", None)
        recursive = _optional_bool(arguments, "recursive", True)
        requested_max = _optional_positive_int(arguments, "max_results", None)
        maximum = min(
            requested_max or self._workspace.policy.max_list_results,
            self._workspace.policy.max_list_results,
        )
        directory = resolve_workspace_path(self._workspace, raw_path, allow_root=True)
        if not directory.is_dir():
            raise WorkspacePathTypeError("The requested path is not a directory.")
        if pattern is not None:
            _validate_glob(pattern)

        entries: list[dict[str, JSONValue]] = []
        pending: list[Path] = [directory]
        visited: set[Path] = set()
        truncated = False
        while pending and not truncated:
            current = pending.pop()
            resolved_current = current.resolve(strict=True)
            if resolved_current in visited:
                continue
            visited.add(resolved_current)
            try:
                children = sorted(current.iterdir(), key=lambda item: item.name)
            except OSError as error:
                raise WorkspaceAccessDeniedError(
                    "Workspace directory cannot be listed."
                ) from error
            for child in children:
                relative = relative_workspace_path(self._workspace, child)
                if not policy_allows_path(self._workspace, child):
                    continue
                try:
                    resolved = resolve_workspace_path(
                        self._workspace, relative.as_posix()
                    )
                except WorkspaceError:
                    continue
                kind = "directory" if resolved.is_dir() else "file"
                if pattern is None or relative.match(pattern):
                    if len(entries) >= maximum:
                        truncated = True
                        break
                    entries.append({"path": relative.as_posix(), "kind": kind})
                if recursive and kind == "directory":
                    pending.append(child)
        entries.sort(key=lambda item: str(item["path"]))
        content: JSONObject = {
            "entries": cast(JSONValue, entries),
            "truncated": truncated,
        }
        return ToolExecutionResult(
            content=content,
        )


def _workspace_error(error: WorkspaceError) -> ToolExecutionResult:
    return ToolExecutionResult(
        content={"error": {"code": type(error).__name__, "message": str(error)}},
        is_error=True,
    )


def _optional_string(
    arguments: JSONObject, name: str, default: str | None
) -> str | None:
    value = arguments.get(name, default)
    if value is None:
        return None
    if not isinstance(value, str):
        raise WorkspaceAccessDeniedError(f"Argument '{name}' must be a string.")
    return value


def _optional_bool(arguments: JSONObject, name: str, default: bool) -> bool:
    value = arguments.get(name, default)
    if not isinstance(value, bool):
        raise WorkspaceAccessDeniedError(f"Argument '{name}' must be a boolean.")
    return value


def _optional_positive_int(
    arguments: JSONObject, name: str, default: int | None
) -> int | None:
    value = arguments.get(name, default)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise WorkspaceAccessDeniedError(
            f"Argument '{name}' must be a positive integer."
        )
    return value


def _validate_glob(pattern: str) -> None:
    if not pattern or "\x00" in pattern:
        raise WorkspaceAccessDeniedError("Glob pattern is invalid.")
    normalized = pattern.replace("\\", "/")
    if normalized.startswith("/") or any(
        part == ".." for part in PurePosixPath(normalized).parts
    ):
        raise WorkspaceAccessDeniedError("Glob pattern must stay within the workspace.")
    # Validation deliberately shares the portable path grammar, but glob tokens
    # are only matched against already-contained paths.
    normalize_workspace_path(".", allow_root=True)
