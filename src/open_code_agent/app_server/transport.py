"""Async newline-delimited JSON transport with stdout-only protocol output."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from typing import BinaryIO

from open_code_agent.app_server.limits import AppServerLimits


class TransportError(Exception):
    """A safe framing failure suitable for a JSON-RPC parse/size error."""


class AsyncLineTransport:
    """Injectable byte-line transport; it never owns or closes supplied streams."""

    def __init__(
        self,
        read_line: Callable[[], Awaitable[bytes]],
        write: Callable[[bytes], Awaitable[None]],
        limits: AppServerLimits,
    ) -> None:
        self._read_line = read_line
        self._write = write
        self._limits = limits
        self._write_lock = asyncio.Lock()

    async def read_message(self) -> object | None:
        """Read one complete object or ``None`` at EOF, validating before dispatch."""
        raw = await self._read_line()
        if raw == b"":
            return None
        if len(raw) > self._limits.max_input_message_bytes:
            raise TransportError("Input message exceeds the configured size limit.")
        if raw.endswith(b"\n"):
            raw = raw[:-1]
            if raw.endswith(b"\r"):
                raw = raw[:-1]
        if not raw:
            raise TransportError("Empty input lines are not JSON-RPC messages.")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise TransportError("Input is not valid UTF-8.") from error
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as error:
            raise TransportError("Input is not valid JSON.") from error
        if not isinstance(decoded, dict):
            raise TransportError("JSON-RPC messages must be JSON objects.")
        return decoded

    async def send(self, message: object) -> None:
        """Serialize one compact protocol line atomically with respect to writers."""
        try:
            encoded = (
                json.dumps(
                    message, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
                + b"\n"
            )
        except (TypeError, ValueError) as error:
            raise TransportError("Protocol message could not be serialized.") from error
        if len(encoded) > self._limits.max_output_message_bytes:
            raise TransportError("Output message exceeds the configured size limit.")
        async with self._write_lock:
            await self._write(encoded)


class StdioLineTransport(AsyncLineTransport):
    """Controlled stdio boundary used by the CLI without emitting banners."""

    def __init__(self, limits: AppServerLimits) -> None:
        async def read_line() -> bytes:
            return await asyncio.to_thread(sys.stdin.buffer.readline)

        async def write(data: bytes) -> None:
            def write_sync() -> None:
                stream: BinaryIO = sys.stdout.buffer
                stream.write(data)
                stream.flush()

            await asyncio.to_thread(write_sync)

        super().__init__(read_line, write, limits)
