# Read-Only Workspace Tools

Workspace tools are explicit capabilities: construct one `WorkspaceContext` with an absolute existing repository root, then bind each tool to it. The model never selects a root and `ToolExecutionContext` remains limited to run/call metadata.

```python
from open_code_agent.tools import (
    ListFilesTool,
    ReadFileTool,
    SearchCodeTool,
    ToolRegistry,
)
from open_code_agent.workspace import WorkspaceContext, WorkspacePolicy

workspace = WorkspaceContext(
    root="/absolute/path/to/repository", policy=WorkspacePolicy()
)
tools = ToolRegistry()
tools.register(ListFilesTool(workspace))
tools.register(ReadFileTool(workspace))
tools.register(SearchCodeTool(workspace))
```

## Boundary and policy

Every tool uses the same resolver. Tool paths must be workspace-relative paths (except `.` for directory search/listing); POSIX absolute, Windows drive/UNC, NUL-containing, and traversal paths are rejected. The resolver canonicalizes targets and verifies containment rather than using string prefixes. Symlinks are denied by default. With `follow_symlinks=True`, a symlink is allowed only when its resolved target remains below the canonical root.

The policy excludes repository metadata, virtual environments, dependency and build/cache directories by default. Hidden paths are omitted from listing and search unless `include_hidden_files=True`. Likely sensitive files such as `.env`, key/certificate files, SSH private-key names, and cloud credentials are never listed, read, or searched. `.env.example`, `.env.sample`, and `.env.template` can be allowed as templates. These filename rules reduce accidental disclosure; they are not a complete secret scanner.

## Outputs and limits

`list_files` returns sorted workspace-relative `/` paths and entry kinds. It includes files and directories, never the workspace root. Its optional glob is matched only against already-contained paths.

`read_file` returns a relative path, selected one-based inclusive line range, total line count, and structured numbered lines. UTF-8 and UTF-8 BOM are accepted; NUL-containing binary data and invalid UTF-8 are rejected. File size, selected line count, line length, and returned UTF-8 bytes are bounded; `truncated` is explicit.

`search_code` uses the external `rg` executable. It searches literal text by default and enables regular expressions only with `is_regex=True`. It invokes ripgrep through an argument list with explicit workspace `cwd`, a small process environment, no shell, policy-derived glob exclusions, bounded output, and a timeout. Results are sorted structured records containing relative path, line, optional column, and a bounded matching line. Missing `rg`, invalid regexes, timeouts, and nonstandard process failures become safe structured errors.

Cancellation terminates and reaps a running ripgrep process before propagating `asyncio.CancelledError`. These tools are read-only: they create no files and perform no edits, Git operation, terminal command facility, or network access. Reading can still update access times on some operating systems.

The checks are not a complete sandbox. A malicious concurrent local process can replace filesystem objects between resolution and use; the implementation does not claim to eliminate that operating-system race.
