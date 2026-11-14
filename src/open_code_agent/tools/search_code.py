"""Bounded ripgrep-backed, read-only workspace search tool."""

import asyncio
import json
import os
import shutil
from typing import cast

from open_code_agent.protocol import JSONObject, JSONValue
from open_code_agent.tools.base import (
    ToolExecutionContext,
    ToolExecutionResult,
    ToolRisk,
)
from open_code_agent.tools.list_files import (
    _optional_bool,
    _optional_positive_int,
    _optional_string,
    _validate_glob,
    _workspace_error,
)
from open_code_agent.workspace import (
    SearchError,
    SearchTimeoutError,
    WorkspaceAccessDeniedError,
    WorkspaceContext,
    WorkspaceError,
    WorkspacePathTypeError,
)
from open_code_agent.workspace.files import policy_allows_path, truncate_text
from open_code_agent.workspace.paths import (
    normalize_workspace_path,
    resolve_workspace_path,
)


class SearchCodeTool:
    """Search one bound workspace with an argument-list-only ripgrep process."""

    name = "search_code"
    description = (
        "Search visible workspace text with bounded literal or regex matching."
    )
    input_schema: JSONObject = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "path": {"type": "string"},
            "is_regex": {"type": "boolean"},
            "case_sensitive": {"type": "boolean"},
            "glob": {"type": "string"},
            "max_results": {"type": "integer"},
        },
        "additionalProperties": False,
    }
    risk = ToolRisk.READ

    def __init__(
        self, workspace: WorkspaceContext, *, rg_executable: str | None = None
    ) -> None:
        self._workspace = workspace
        self._rg_executable = rg_executable

    async def execute(
        self, arguments: JSONObject, context: ToolExecutionContext
    ) -> ToolExecutionResult:
        """Run ripgrep safely; expected input/process errors are structured data."""
        del context
        try:
            return await self._search(arguments)
        except asyncio.CancelledError:
            raise
        except WorkspaceError as error:
            return _workspace_error(error)

    async def _search(self, arguments: JSONObject) -> ToolExecutionResult:
        query = _optional_string(arguments, "query", None)
        if query is None or not query:
            raise SearchError("Search query must not be empty.")
        raw_path = _optional_string(arguments, "path", ".")
        assert raw_path is not None
        is_regex = _optional_bool(arguments, "is_regex", False)
        case_sensitive = _optional_bool(arguments, "case_sensitive", False)
        glob = _optional_string(arguments, "glob", None)
        if glob is not None:
            _validate_glob(glob)
        requested_max = _optional_positive_int(arguments, "max_results", None)
        maximum = min(
            requested_max or self._workspace.policy.max_search_results,
            self._workspace.policy.max_search_results,
        )
        target = resolve_workspace_path(self._workspace, raw_path, allow_root=True)
        if not target.is_dir() and not target.is_file():
            raise WorkspacePathTypeError("The requested search path is not searchable.")
        if not policy_allows_path(self._workspace, target):
            raise WorkspaceAccessDeniedError("Access to this workspace path is denied.")
        target_relative = normalize_workspace_path(raw_path, allow_root=True).as_posix()
        executable = self._rg_executable or shutil.which("rg")
        if executable is None:
            raise SearchError("ripgrep (rg) is required for code search.")
        args = self._arguments(
            executable,
            query,
            target_relative,
            is_regex=is_regex,
            case_sensitive=case_sensitive,
            glob=glob,
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                cwd=self._workspace.root,
                env={"PATH": os.defpath, "LC_ALL": "C"},
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=self._workspace.policy.max_output_bytes + 1,
            )
        except OSError as error:
            raise SearchError("ripgrep could not be started.") from error
        assert process.stdout is not None
        assert process.stderr is not None
        stderr_task = asyncio.create_task(
            _drain(process.stderr, self._workspace.policy.max_output_bytes)
        )
        try:
            async with asyncio.timeout(self._workspace.policy.search_timeout_seconds):
                records, truncated = await self._collect_matches(process, maximum)
                returncode = await process.wait()
                await stderr_task
        except asyncio.CancelledError:
            await _terminate_and_reap(process, stderr_task)
            raise
        except TimeoutError as error:
            await _terminate_and_reap(process, stderr_task)
            raise SearchTimeoutError(
                "Workspace search exceeded its time limit."
            ) from error
        except Exception:
            await _terminate_and_reap(process, stderr_task)
            raise
        if not truncated and returncode not in {0, 1}:
            raise SearchError("ripgrep could not complete this search.")
        content: JSONObject = {
            "matches": cast(JSONValue, records),
            "truncated": truncated,
        }
        return ToolExecutionResult(content=content)

    def _arguments(
        self,
        executable: str,
        query: str,
        target_relative: str,
        *,
        is_regex: bool,
        case_sensitive: bool,
        glob: str | None,
    ) -> list[str]:
        policy = self._workspace.policy
        args = [
            executable,
            "--json",
            "--no-messages",
            "--line-number",
            "--column",
            "--sort",
            "path",
        ]
        args.append("--case-sensitive" if case_sensitive else "--ignore-case")
        if not is_regex:
            args.append("--fixed-strings")
        if policy.include_hidden_files:
            args.append("--hidden")
        if not policy.respect_gitignore:
            args.append("--no-ignore")
        for directory in policy.excluded_directory_names:
            args.extend(["--glob", f"!**/{directory}/**"])
            args.extend(["--glob", f"!{directory}/**"])
        for pattern in [
            ".env",
            ".env.*",
            "*.pem",
            "*.key",
            "id_rsa",
            "id_ed25519",
            "credentials",
            "credentials.json",
            "**/.aws/credentials",
            *policy.denied_filename_patterns,
        ]:
            args.extend(["--glob", f"!{pattern}"])
        if glob is not None:
            args.extend(["--glob", glob])
        # Both model-supplied positional values follow ``--``; no shell is used.
        args.extend(["--", query, target_relative])
        return args

    async def _collect_matches(
        self, process: asyncio.subprocess.Process, maximum: int
    ) -> tuple[list[dict[str, JSONValue]], bool]:
        assert process.stdout is not None
        records: list[dict[str, JSONValue]] = []
        output_bytes = 0
        truncated = False
        try:
            async with asyncio.timeout(self._workspace.policy.search_timeout_seconds):
                while True:
                    raw = await process.stdout.readline()
                    if not raw:
                        break
                    output_bytes += len(raw)
                    if output_bytes > self._workspace.policy.max_output_bytes:
                        truncated = True
                        await _terminate_process(process)
                        break
                    record = _parse_match(raw)
                    if record is None:
                        continue
                    relative = record.pop("_path")
                    assert isinstance(relative, str)
                    try:
                        resolved = resolve_workspace_path(self._workspace, relative)
                    except WorkspaceError:
                        continue
                    if not policy_allows_path(self._workspace, resolved):
                        continue
                    if len(records) >= maximum:
                        truncated = True
                        await _terminate_process(process)
                        break
                    line, line_truncated = truncate_text(
                        str(record["text"]), self._workspace.policy.max_line_length
                    )
                    record["text"] = line
                    record["line_truncated"] = line_truncated
                    records.append(record)
        except (asyncio.LimitOverrunError, ValueError) as error:
            raise SearchError("ripgrep produced an oversized result line.") from error
        records.sort(
            key=lambda item: (
                str(item["path"]).encode("utf-8"),
                item["line"] if isinstance(item["line"], int) else 0,
                item["column"] if isinstance(item["column"], int) else 0,
                str(item["text"]),
            )
        )
        return records, truncated


def _parse_match(raw: bytes) -> dict[str, JSONValue] | None:
    """Parse one rg JSON match record without exposing malformed raw output."""
    try:
        event = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(event, dict) or event.get("type") != "match":
        return None
    data = event.get("data")
    if not isinstance(data, dict):
        return None
    path_data = data.get("path")
    lines_data = data.get("lines")
    line_number = data.get("line_number")
    if (
        not isinstance(path_data, dict)
        or not isinstance(path_data.get("text"), str)
        or not isinstance(lines_data, dict)
        or not isinstance(lines_data.get("text"), str)
        or not isinstance(line_number, int)
    ):
        return None
    raw_path = path_data["text"]
    try:
        relative = normalize_workspace_path(raw_path).as_posix()
    except WorkspaceError:
        return None
    line = lines_data["text"].rstrip("\r\n")
    # The first byte-offset is a useful, portable 1-based column approximation.
    column: int | None = None
    submatches = data.get("submatches")
    if isinstance(submatches, list) and submatches:
        first = submatches[0]
        if isinstance(first, dict) and isinstance(first.get("start"), int):
            column = first["start"] + 1
    return {
        "_path": relative,
        "path": relative,
        "line": line_number,
        "column": column,
        "text": line,
    }


async def _drain(reader: asyncio.StreamReader, maximum: int) -> bytes:
    """Drain stderr fully so a failing process cannot block on a full pipe."""
    captured = bytearray()
    while chunk := await reader.read(4096):
        if len(captured) < maximum:
            captured.extend(chunk[: maximum - len(captured)])
    return bytes(captured)


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.terminate()


async def _terminate_and_reap(
    process: asyncio.subprocess.Process, stderr_task: asyncio.Task[bytes]
) -> None:
    await _terminate_process(process)
    try:
        await process.wait()
    finally:
        if not stderr_task.done():
            await stderr_task
