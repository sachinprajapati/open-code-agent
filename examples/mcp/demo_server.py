"""A harmless local stdio MCP server for manual extension testing.

This development fixture intentionally exposes only three deterministic tools.
It does not access the shell, filesystem, environment, network, subprocesses,
or secrets.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

SERVER_INFO = {
    "name": "OpenCodeAgent local demo MCP server",
    "purpose": "development-only manual acceptance testing",
    "transport": "stdio",
}

mcp = FastMCP("OpenCodeAgent Local Demo MCP Server")


@mcp.tool()
def echo(message: str) -> str:
    """Return the supplied message with a fixed prefix."""
    return f"echo: {message}"


@mcp.tool()
def add_numbers(left: int, right: int) -> int:
    """Return the sum of two integers."""
    return left + right


@mcp.tool()
def server_info() -> dict[str, str]:
    """Identify this fixed, development-only local demo server."""
    return dict(SERVER_INFO)


if __name__ == "__main__":
    mcp.run(transport="stdio")
