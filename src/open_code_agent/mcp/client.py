"""MCP lifecycle, discovery and safe tool adaptation."""
# ruff: noqa: E501

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from typing import cast

from open_code_agent.mcp.models import MCPConnectionState, MCPServerConfig
from open_code_agent.mcp.stdio import MCPTransportError, StdioMCPTransport
from open_code_agent.protocol import JSONObject, JSONValue
from open_code_agent.tools import (
    ToolExecutionContext,
    ToolExecutionResult,
    ToolRegistry,
    ToolRisk,
)

_MAX_ARGUMENT_BYTES = 65_536
_MAX_RESPONSE_BYTES = 262_144
MCPApprovalHandler = Callable[
    [MCPServerConfig, str, JSONObject, ToolExecutionContext], Awaitable[bool]
]


def _bounded_json(value: object, limit: int) -> JSONValue:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        normalized = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise MCPTransportError("MCP payload is not JSON-compatible.") from error
    if len(encoded.encode("utf-8")) > limit:
        raise MCPTransportError("MCP payload exceeds size limit.")
    return cast(JSONValue, normalized)


def sanitize_schema(value: object) -> JSONObject:
    """Keep only the schema subset understood by the existing registry."""
    if not isinstance(value, Mapping):
        return {"type": "object", "properties": {}, "additionalProperties": True}
    properties: dict[str, JSONValue] = {}
    raw_properties = value.get("properties")
    if isinstance(raw_properties, Mapping):
        for name, item in raw_properties.items():
            if isinstance(name, str) and isinstance(item, Mapping):
                kind = item.get("type")
                if kind in {"string", "number", "integer", "boolean", "object", "array"}:
                    properties[name] = {"type": kind}
    required = value.get("required")
    return {
        "type": "object",
        "properties": properties,
        "required": [item for item in required if isinstance(item, str) and item in properties]
        if isinstance(required, list)
        else [],
        "additionalProperties": True,
    }


class MCPTool:
    """One namespaced MCP method exposed through the ordinary tool registry."""

    def __init__(self, client: ManagedMCPServer, name: str, description: str, schema: JSONObject) -> None:
        self._client, self._mcp_name, self._description, self._schema = client, name, description, schema

    @property
    def name(self) -> str:
        return f"mcp.{self._client.config.server_id}.{self._mcp_name}"

    @property
    def description(self) -> str:
        return self._description

    @property
    def input_schema(self) -> JSONObject:
        return deepcopy(self._schema)

    @property
    def risk(self) -> ToolRisk:
        return ToolRisk.EXECUTE

    async def execute(self, arguments: JSONObject, context: ToolExecutionContext) -> ToolExecutionResult:
        return await self._client.call_tool(self._mcp_name, arguments, context)


class ManagedMCPServer:
    """An explicitly enabled server, never started from workspace/model data."""

    def __init__(
        self,
        config: MCPServerConfig,
        resolve_secret: Callable[[str], Awaitable[str]],
        *,
        approval_handler: MCPApprovalHandler | None = None,
    ) -> None:
        self.config = config.model_copy(deep=True)
        self._transport = StdioMCPTransport(self.config, resolve_secret)
        self._approval_handler = approval_handler
        self.state = MCPConnectionState.DISCONNECTED
        self.last_error: str | None = None
        self.tools: tuple[MCPTool, ...] = ()

    async def connect(self) -> tuple[MCPTool, ...]:
        if not self.config.enabled:
            raise MCPTransportError("MCP server is disabled.")
        self.state = MCPConnectionState.CONNECTING
        try:
            await self._transport.start()
            await self._transport.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "open-code-agent", "version": "0.1.0"}})
            discovered = await self._transport.request("tools/list", {})
            raw = discovered.get("tools")
            if not isinstance(raw, list):
                raise MCPTransportError("MCP tools/list result is invalid.")
            tools: list[MCPTool] = []
            for item in raw:
                if not isinstance(item, Mapping):
                    continue
                name, description = item.get("name"), item.get("description", "")
                if isinstance(name, str) and name and all(c.isalnum() or c in "_.-" for c in name):
                    tools.append(MCPTool(self, name, description if isinstance(description, str) and description.strip() else "Untrusted MCP tool.", sanitize_schema(item.get("inputSchema"))))
            self.tools = tuple(tools)
            self.last_error = None
            self.state = MCPConnectionState.CONNECTED
            return self.tools
        except Exception as error:
            self.state = MCPConnectionState.ERROR
            self.last_error = "MCP server connection failed."
            await self._transport.close()
            if isinstance(error, MCPTransportError):
                raise
            raise MCPTransportError("MCP server connection failed.") from error

    async def call_tool(
        self, name: str, arguments: JSONObject, context: ToolExecutionContext
    ) -> ToolExecutionResult:
        safe_arguments = _bounded_json(arguments, _MAX_ARGUMENT_BYTES)
        assert isinstance(safe_arguments, dict)
        if self._approval_handler is None or not await self._approval_handler(
            self.config, name, safe_arguments, context
        ):
            return ToolExecutionResult(
                content={"error": "MCP tool call was not approved."}, is_error=True
            )
        result = await self._transport.request("tools/call", {"name": name, "arguments": safe_arguments})
        return ToolExecutionResult(content=_bounded_json(result, _MAX_RESPONSE_BYTES))

    async def close(self) -> None:
        await self._transport.close()
        self.state = MCPConnectionState.DISCONNECTED


async def register_mcp_server(server: ManagedMCPServer, registry: ToolRegistry) -> tuple[MCPTool, ...]:
    """Connect an explicitly enabled server and atomically register its tools.

    A collision is a configuration error: no native or previously discovered
    tool is replaced, and the newly owned process is stopped again.
    """
    tools = await server.connect()
    registered: list[MCPTool] = []
    try:
        for tool in tools:
            registry.register(tool)
            registered.append(tool)
    except Exception:
        for tool in registered:
            registry.unregister(tool.name)
        await server.close()
        raise
    return tuple(registered)
