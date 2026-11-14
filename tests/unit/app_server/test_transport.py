import pytest

from open_code_agent.app_server.limits import AppServerLimits
from open_code_agent.app_server.transport import AsyncLineTransport, TransportError


async def test_transport_accepts_lf_and_crlf_and_serializes_one_line() -> None:
    lines = iter([b'{"jsonrpc":"2.0"}\r\n', b'{"jsonrpc":"2.0"}\n'])
    written: list[bytes] = []

    async def read_line() -> bytes:
        return next(lines)

    async def write(data: bytes) -> None:
        written.append(data)

    transport = AsyncLineTransport(read_line, write, AppServerLimits())
    assert await transport.read_message() == {"jsonrpc": "2.0"}
    assert await transport.read_message() == {"jsonrpc": "2.0"}
    await transport.send({"jsonrpc": "2.0", "method": "x"})
    assert written == [b'{"jsonrpc":"2.0","method":"x"}\n']


async def test_transport_rejects_invalid_utf8_and_oversized_input() -> None:
    async def invalid_utf8() -> bytes:
        return b"\xff\n"

    async def write(_: bytes) -> None:
        return None

    with pytest.raises(TransportError):
        await AsyncLineTransport(invalid_utf8, write, AppServerLimits()).read_message()

    async def oversized() -> bytes:
        return b"x" * 8

    with pytest.raises(TransportError):
        await AsyncLineTransport(
            oversized, write, AppServerLimits(max_input_message_bytes=4)
        ).read_message()
