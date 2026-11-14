"""Read-only workspace tool behavior over temporary repositories."""

import asyncio
import shutil
from pathlib import Path

import pytest

from open_code_agent.protocol import JSONObject, JSONValue
from open_code_agent.tools import (
    ListFilesTool,
    ReadFileTool,
    SearchCodeTool,
    ToolExecutionContext,
)
from open_code_agent.workspace import WorkspaceContext, WorkspacePolicy


def context() -> ToolExecutionContext:
    """Build the small safe context required by direct tool execution."""
    return ToolExecutionContext(run_id="run", step=1, tool_call_id="call")


def snapshot(root: Path) -> dict[str, bytes]:
    """Capture files before and after operations, ignoring symlink fixtures."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def object_content(value: JSONValue) -> JSONObject:
    """Narrow JSON-compatible tool content for assertions."""
    assert isinstance(value, dict)
    return value


def object_list(value: JSONValue) -> list[JSONObject]:
    """Narrow a JSON array of object records for assertions."""
    assert isinstance(value, list)
    result: list[JSONObject] = []
    for item in value:
        assert isinstance(item, dict)
        result.append(item)
    return result


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    """Create a small repository-shaped tree with secrets and ignored content."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def Hello():\n    return 'needle'\n")
    (tmp_path / "README.md").write_text("alpha needle\nbeta\ngamma\n")
    (tmp_path / ".env").write_text("TOKEN=secret")
    (tmp_path / ".env.example").write_text("TOKEN=example")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.js").write_text("needle")
    return tmp_path


async def test_list_files_is_sorted_bounded_and_omits_denied_paths(
    workspace_root: Path,
) -> None:
    before = snapshot(workspace_root)
    tool = ListFilesTool(WorkspaceContext(root=workspace_root))
    result = await tool.execute({"recursive": True, "max_results": 20}, context())
    content = object_content(result.content)

    assert not result.is_error
    assert isinstance(content, dict)
    paths = [str(entry["path"]) for entry in object_list(content["entries"])]
    assert paths == sorted(paths)
    assert ".env" not in paths
    assert ".env.example" in paths  # explicit non-secret template exception
    assert "node_modules/ignored.js" not in paths
    assert all(not str(path).startswith("/") for path in paths)
    assert snapshot(workspace_root) == before


async def test_read_file_bounds_text_and_sensitive_access(workspace_root: Path) -> None:
    policy = WorkspacePolicy(max_read_lines=2, max_line_length=3, max_output_bytes=10)
    tool = ReadFileTool(WorkspaceContext(root=workspace_root, policy=policy))
    result = await tool.execute({"path": "README.md"}, context())
    assert not result.is_error
    content = object_content(result.content)
    assert content["truncated"] is True
    assert object_list(content["lines"])[0]["text"] == "alp"

    denied = await tool.execute({"path": ".env"}, context())
    assert denied.is_error
    assert "secret" not in str(denied.content)
    invalid = await tool.execute(
        {"path": "README.md", "start_line": 3, "end_line": 1}, context()
    )
    assert invalid.is_error


async def test_read_file_rejects_binary_invalid_encoding_and_large_files(
    workspace_root: Path,
) -> None:
    (workspace_root / "binary.bin").write_bytes(b"a\x00b")
    (workspace_root / "bad.txt").write_bytes(b"\xff")
    (workspace_root / "large.txt").write_text("0123456789")
    tool = ReadFileTool(
        WorkspaceContext(
            root=workspace_root, policy=WorkspacePolicy(max_file_size_bytes=5)
        )
    )
    for name in ["binary.bin", "bad.txt", "large.txt", "missing.txt"]:
        result = await tool.execute({"path": name}, context())
        assert result.is_error


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep is not installed")
async def test_search_is_bounded_safe_and_excludes_secrets(
    workspace_root: Path,
) -> None:
    before = snapshot(workspace_root)
    tool = SearchCodeTool(
        WorkspaceContext(
            root=workspace_root, policy=WorkspacePolicy(max_search_results=1)
        )
    )
    literal = await tool.execute({"query": "needle"}, context())
    assert not literal.is_error
    literal_content = object_content(literal.content)
    assert literal_content["truncated"] is True
    assert object_list(literal_content["matches"])[0]["path"] == "README.md"

    insensitive = await tool.execute({"query": "hello"}, context())
    assert not insensitive.is_error
    sensitive = await tool.execute({"query": "secret"}, context())
    assert sensitive.content == {"matches": [], "truncated": False}
    attack = await tool.execute({"query": "$(touch bad)"}, context())
    assert not attack.is_error
    assert snapshot(workspace_root) == before


async def test_search_cancellation_propagates(workspace_root: Path) -> None:
    tool = SearchCodeTool(
        WorkspaceContext(root=workspace_root), rg_executable="/bin/sleep"
    )
    task = asyncio.create_task(tool.execute({"query": "3"}, context()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep is not installed")
async def test_search_order_and_truncation_are_deterministic(tmp_path: Path) -> None:
    """Selection is bytewise-path ordered before the bounded result limit applies."""
    files = {
        "zeta.txt": "needle\nneedle\n",
        "Alpha.txt": "needle\n",
        "a space.txt": "needle\n",
        "alpha.txt": "needle\n",
        "beta.txt": "needle\n",
    }
    for name, contents in files.items():
        (tmp_path / name).write_text(contents)
    tool = SearchCodeTool(
        WorkspaceContext(root=tmp_path, policy=WorkspacePolicy(max_search_results=20))
    )

    results = [await tool.execute({"query": "needle"}, context()) for _ in range(10)]
    snapshots = [object_content(result.content) for result in results]
    assert all(not result.is_error for result in results)
    assert all(snapshot == snapshots[0] for snapshot in snapshots)
    assert [
        (match["path"], match["line"], match["column"])
        for match in object_list(snapshots[0]["matches"])
    ] == [
        ("Alpha.txt", 1, 1),
        ("a space.txt", 1, 1),
        ("alpha.txt", 1, 1),
        ("beta.txt", 1, 1),
        ("zeta.txt", 1, 1),
        ("zeta.txt", 2, 1),
    ]

    bounded = SearchCodeTool(
        WorkspaceContext(root=tmp_path, policy=WorkspacePolicy(max_search_results=2))
    )
    bounded_results = [
        await bounded.execute({"query": "needle"}, context()) for _ in range(10)
    ]
    bounded_snapshots = [object_content(result.content) for result in bounded_results]
    assert all(snapshot == bounded_snapshots[0] for snapshot in bounded_snapshots)
    assert bounded_snapshots[0]["truncated"] is True
    assert [
        match["path"] for match in object_list(bounded_snapshots[0]["matches"])
    ] == [
        "Alpha.txt",
        "a space.txt",
    ]

    no_matches = await tool.execute({"query": "absent"}, context())
    assert no_matches.content == {"matches": [], "truncated": False}
