"""A minimal bounded JSON-RPC transport for extension-owned stdio MCP processes."""
# ruff: noqa: E501

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
from collections.abc import Awaitable, Callable
from typing import Any

from open_code_agent.mcp.models import MCPServerConfig


class MCPTransportError(RuntimeError):
    """Safe transport failure; never includes command environment values."""


class StdioMCPTransport:
    """Own a structured-argv child process and correlate JSON-RPC requests."""

    def __init__(
        self,
        config: MCPServerConfig,
        resolve_secret: Callable[[str], Awaitable[str]],
        *,
        max_message_bytes: int = 1_048_576,
    ) -> None:
        self._config = config
        self._resolve_secret = resolve_secret
        self._max_message_bytes = max_message_bytes
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 0
        self._write_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        if self.running:
            return
        env = {"PATH": os.defpath, "LANG": "C.UTF-8"}
        for name, reference in self._config.environment_secret_refs.items():
            env[name] = await self._resolve_secret(reference)
        command = self._config.command
        if os.path.sep not in command:
            command = shutil.which(command) or command
        try:
            self._process = await asyncio.create_subprocess_exec(
                command,
                *self._config.arguments,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=self._config.working_directory,
                env=env,
            )
        except (OSError, ValueError) as error:
            raise MCPTransportError("MCP server could not be started.") from error
        self._reader = asyncio.create_task(self._read_loop())

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.running or self._process is None or self._process.stdin is None:
            raise MCPTransportError("MCP server is not connected.")
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > self._max_message_bytes:
            self._pending.pop(request_id, None)
            raise MCPTransportError("MCP request exceeds size limit.")
        try:
            async with self._write_lock:
                self._process.stdin.write(payload + b"\n")
                await self._process.stdin.drain()
            async with asyncio.timeout(self._config.timeout_seconds):
                response = await future
        except TimeoutError as error:
            self._pending.pop(request_id, None)
            raise MCPTransportError("MCP request timed out.") from error
        except asyncio.CancelledError:
            self._pending.pop(request_id, None)
            raise
        if "error" in response:
            raise MCPTransportError("MCP server returned an error.")
        result = response.get("result")
        if not isinstance(result, dict):
            raise MCPTransportError("MCP server returned an invalid result.")
        return result

    async def close(self) -> None:
        process, self._process = self._process, None
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        for future in self._pending.values():
            if not future.done():
                future.set_exception(MCPTransportError("MCP transport closed."))
        self._pending.clear()
        if process is not None and process.returncode is None:
            process.terminate()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=3)
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def _read_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    raise MCPTransportError("MCP server exited.")
                if len(line) > self._max_message_bytes:
                    raise MCPTransportError("MCP response exceeds size limit.")
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as error:
                    raise MCPTransportError("MCP server emitted invalid JSON.") from error
                if not isinstance(message, dict) or not isinstance(message.get("id"), int):
                    continue
                future = self._pending.pop(message["id"], None)
                if future is not None and not future.done():
                    future.set_result(message)
        except asyncio.CancelledError:
            raise
        except MCPTransportError as error:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
            self._pending.clear()
