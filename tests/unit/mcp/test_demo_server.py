"""Tests for the harmless local MCP demo fixture."""

import sys
from pathlib import Path

from open_code_agent.mcp import ManagedMCPServer, MCPServerConfig

sys.path.insert(0, str(Path(__file__).parents[3]))

from examples.mcp.demo_server import (  # noqa: E402
    SERVER_INFO,
    add_numbers,
    echo,
    mcp,
    server_info,
)


async def test_server_exposes_exactly_the_three_demo_tools() -> None:
    tools = await mcp.list_tools()
    assert [tool.name for tool in tools] == ["echo", "add_numbers", "server_info"]


def test_ui_stdio_transport_value_is_accepted() -> None:
    config = MCPServerConfig.model_validate(
        {
            "server_id": "local-demo",
            "display_name": "OpenCodeAgent local demo",
            "transport": "stdio",
            "command": "uv",
        }
    )
    assert config.transport.value == "stdio"


async def test_client_connects_with_the_documented_uv_configuration() -> None:
    async def resolve_secret(_: str) -> str:
        raise AssertionError("The demo server does not need secrets.")

    server = ManagedMCPServer(
        MCPServerConfig(
            server_id="local-demo",
            display_name="OpenCodeAgent local demo",
            command="uv",
            arguments=["run", "python", "examples/mcp/demo_server.py"],
            working_directory=str(Path(__file__).parents[3]),
            enabled=True,
        ),
        resolve_secret,
    )
    try:
        tools = await server.connect()
        assert [tool.name for tool in tools] == [
            "mcp.local-demo.echo",
            "mcp.local-demo.add_numbers",
            "mcp.local-demo.server_info",
        ]
    finally:
        await server.close()


def test_echo_is_deterministic() -> None:
    assert echo("hello") == "echo: hello"


def test_add_numbers_returns_the_integer_sum() -> None:
    assert add_numbers(20, 22) == 42


def test_server_info_is_fixed_and_json_safe() -> None:
    assert server_info() == SERVER_INFO
    assert server_info() is not SERVER_INFO
