"""Read-only bounded UTF-8 workspace file reader."""

from typing import cast

from open_code_agent.protocol import JSONObject, JSONValue
from open_code_agent.tools.base import (
    ToolExecutionContext,
    ToolExecutionResult,
    ToolRisk,
)
from open_code_agent.tools.list_files import (
    _optional_positive_int,
    _optional_string,
    _workspace_error,
)
from open_code_agent.workspace import (
    BinaryFileError,
    FileTooLargeError,
    InvalidTextEncodingError,
    WorkspaceAccessDeniedError,
    WorkspaceContext,
    WorkspaceError,
    WorkspacePathTypeError,
)
from open_code_agent.workspace.files import (
    policy_allows_path,
    require_non_sensitive,
    truncate_text,
    truncate_utf8,
)
from open_code_agent.workspace.paths import resolve_workspace_path


class ReadFileTool:
    """Read a bounded line range from one safe text file in a bound workspace."""

    name = "read_file"
    description = "Read a bounded range of a visible UTF-8 workspace text file."
    input_schema: JSONObject = {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
        },
        "additionalProperties": False,
    }
    risk = ToolRisk.READ

    def __init__(self, workspace: WorkspaceContext) -> None:
        self._workspace = workspace

    async def execute(
        self, arguments: JSONObject, context: ToolExecutionContext
    ) -> ToolExecutionResult:
        """Return structured numbered lines; expected errors never leak content."""
        del context
        try:
            return self._read(arguments)
        except WorkspaceError as error:
            return _workspace_error(error)

    def _read(self, arguments: JSONObject) -> ToolExecutionResult:
        raw_path = _optional_string(arguments, "path", None)
        if raw_path is None or not raw_path:
            raise WorkspaceAccessDeniedError(
                "Argument 'path' must be a non-empty string."
            )
        start = _optional_positive_int(arguments, "start_line", None)
        end = _optional_positive_int(arguments, "end_line", None)
        if start is not None and end is not None and start > end:
            raise WorkspaceAccessDeniedError("start_line must not exceed end_line.")
        path = resolve_workspace_path(self._workspace, raw_path)
        if not path.is_file():
            raise WorkspacePathTypeError("The requested path is not a regular file.")
        if not policy_allows_path(self._workspace, path):
            # Sensitive access is called separately to preserve that safe error.
            require_non_sensitive(self._workspace, path)
            raise WorkspaceAccessDeniedError("Access to this workspace path is denied.")
        relative = require_non_sensitive(self._workspace, path)
        policy = self._workspace.policy
        try:
            size = path.stat().st_size
        except OSError as error:
            raise WorkspaceAccessDeniedError(
                "Workspace file cannot be inspected."
            ) from error
        if size > policy.max_file_size_bytes:
            raise FileTooLargeError("Workspace file exceeds the configured read limit.")
        try:
            with path.open("rb") as handle:
                data = handle.read(policy.max_file_size_bytes + 1)
        except OSError as error:
            raise WorkspaceAccessDeniedError(
                "Workspace file cannot be read."
            ) from error
        if len(data) > policy.max_file_size_bytes:
            raise FileTooLargeError("Workspace file exceeds the configured read limit.")
        if b"\x00" in data:
            raise BinaryFileError("Binary workspace files cannot be returned as text.")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise InvalidTextEncodingError(
                "Workspace file is not valid UTF-8 text."
            ) from error
        lines = text.splitlines()
        total_lines = len(lines)
        selected_start = start or 1
        if selected_start > total_lines and total_lines != 0:
            raise WorkspaceAccessDeniedError(
                "Requested line range is beyond end of file."
            )
        if total_lines == 0 and selected_start > 1:
            raise WorkspaceAccessDeniedError(
                "Requested line range is beyond end of file."
            )
        requested_end = end or total_lines
        if requested_end > total_lines:
            raise WorkspaceAccessDeniedError(
                "Requested line range is beyond end of file."
            )
        selected_end = min(requested_end, selected_start + policy.max_read_lines - 1)
        truncated = selected_end < requested_end
        output_lines: list[dict[str, JSONValue]] = []
        output_bytes = 0
        for number in range(selected_start, selected_end + 1):
            rendered, line_truncated = truncate_text(
                lines[number - 1], policy.max_line_length
            )
            remaining = policy.max_output_bytes - output_bytes
            if remaining <= 0:
                truncated = True
                break
            rendered, byte_truncated = truncate_utf8(rendered, remaining)
            encoded_size = len(rendered.encode("utf-8"))
            output_bytes += encoded_size
            output_lines.append(
                {
                    "number": number,
                    "text": rendered,
                    "truncated": line_truncated or byte_truncated,
                }
            )
            if byte_truncated:
                truncated = True
                break
        content: JSONObject = {
            "path": relative.as_posix(),
            "start_line": selected_start,
            "end_line": selected_start + len(output_lines) - 1,
            "total_lines": total_lines,
            "lines": cast(JSONValue, output_lines),
            "truncated": truncated,
        }
        return ToolExecutionResult(content=content)
