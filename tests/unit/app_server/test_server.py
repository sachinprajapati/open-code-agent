import asyncio
import json
from pathlib import Path

from open_code_agent.app_server.limits import AppServerLimits
from open_code_agent.app_server.server import AppServer
from open_code_agent.app_server.transport import AsyncLineTransport
from open_code_agent.persistence import ProviderProfileStore
from open_code_agent.providers import OpenAICompatibleProfile
from open_code_agent.providers.base import ConfigurationValidationResult


def server_for(store: ProviderProfileStore) -> tuple[AppServer, list[bytes]]:
    output: list[bytes] = []

    async def read_line() -> bytes:
        return b""

    async def write(data: bytes) -> None:
        output.append(data)

    transport = AsyncLineTransport(read_line, write, AppServerLimits())
    return AppServer(transport, profile_store=store), output


async def test_initialize_and_secret_safe_profile_listing(tmp_path: Path) -> None:
    store = ProviderProfileStore(tmp_path / "profiles.json")
    store.add(
        OpenAICompatibleProfile(
            profile_id="local",
            display_name="Local",
            base_url="http://localhost:1234/v1",
            default_model="test-model",
            api_key_secret_ref="env:TEST_SECRET",
        )
    )
    server, _ = server_for(store)

    initialized = await server._dispatch(  # noqa: SLF001 - dispatcher contract test
        "initialize",
        {
            "client_name": "test",
            "client_version": "1",
            "supported_protocol_versions": ["1.0"],
        },
    )
    profiles = await server._dispatch("profiles/list", {})  # noqa: SLF001

    assert initialized["selected_protocol_version"] == "1.0"
    assert profiles == {
        "profiles": [
            {
                "profile_id": "local",
                "display_name": "Local",
                "base_url": "http://localhost:1234/v1",
                "default_model": "test-model",
                "authentication_configured": True,
                "is_default": False,
            }
        ]
    }


async def test_profile_test_processes_editor_secret_response_while_pending(
    tmp_path: Path,
) -> None:
    """A profile request must not block its own server-to-client secret response."""
    store = ProviderProfileStore(tmp_path / "profiles.json")
    store.add(
        OpenAICompatibleProfile(
            profile_id="remote",
            display_name="Remote",
            base_url="https://api.example.test/v1",
            default_model="test-model",
            api_key_secret_ref="vscode:remote",
        )
    )
    inbound: asyncio.Queue[bytes] = asyncio.Queue()
    outbound: list[dict[str, object]] = []

    async def read_line() -> bytes:
        return await inbound.get()

    async def write(data: bytes) -> None:
        outbound.append(json.loads(data))

    transport = AsyncLineTransport(read_line, write, AppServerLimits())
    server: AppServer

    class SecretCheckingProvider:
        async def validate_configuration(self) -> ConfigurationValidationResult:
            await server._editor_secret("vscode:remote")  # noqa: SLF001
            return ConfigurationValidationResult(valid=True)

        async def aclose(self) -> None:
            return None

    server = AppServer(
        transport,
        profile_store=store,
        provider_builder=lambda _: SecretCheckingProvider(),
    )
    serving = asyncio.create_task(server.serve())

    await inbound.put(
        b'{"jsonrpc":"2.0","id":"initialize","method":"initialize",'
        b'"params":{"client_name":"test","client_version":"1",'
        b'"supported_protocol_versions":["1.0"]}}\n'
    )
    for _ in range(20):
        if outbound:
            break
        await asyncio.sleep(0)
    await inbound.put(
        b'{"jsonrpc":"2.0","id":"profile-test","method":"profiles/test",'
        b'"params":{"profile_id":"remote"}}\n'
    )
    for _ in range(20):
        if len(outbound) >= 2:
            break
        await asyncio.sleep(0)
    secret_request = outbound[1]
    assert secret_request["method"] == "secret/resolve"
    await inbound.put(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": secret_request["id"],
                "result": {"secret": "test-key"},
            }
        ).encode()
        + b"\n"
    )
    for _ in range(20):
        if len(outbound) >= 3:
            break
        await asyncio.sleep(0)
    assert outbound[2] == {
        "jsonrpc": "2.0",
        "id": "profile-test",
        "result": {"valid": True, "diagnostic": None},
    }

    await inbound.put(b'{"jsonrpc":"2.0","method":"exit"}\n')
    await serving


async def test_session_lifecycle_is_in_memory(tmp_path: Path) -> None:
    store = ProviderProfileStore(tmp_path / "profiles.json")
    store.add(
        OpenAICompatibleProfile(
            profile_id="local",
            display_name="Local",
            base_url="http://localhost:1234/v1",
            default_model="test-model",
        )
    )
    server, _ = server_for(store)
    await server._dispatch(  # noqa: SLF001
        "initialize",
        {
            "client_name": "test",
            "client_version": "1",
            "supported_protocol_versions": ["1.0"],
        },
    )

    created = await server._dispatch(  # noqa: SLF001
        "session/create",
        {"profile_id": "local", "workspace_path": str(tmp_path), "mode": "plan"},
    )
    status = await server._dispatch(  # noqa: SLF001
        "session/status", {"session_id": created["session_id"]}
    )
    cleared = await server._dispatch(  # noqa: SLF001
        "session/clear", {"session_id": created["session_id"]}
    )
    closed = await server._dispatch(  # noqa: SLF001
        "session/close", {"session_id": created["session_id"]}
    )

    assert created["available_tools"] == ["list_files", "read_file", "search_code"]
    assert status["history_message_count"] == 0
    assert cleared["turn_count"] == 0
    assert closed["closed"] is True


async def test_session_attaches_explicit_connected_mcp_tools(tmp_path: Path) -> None:
    store = ProviderProfileStore(tmp_path / "profiles.json")
    store.add(
        OpenAICompatibleProfile(
            profile_id="local",
            display_name="Local",
            base_url="http://localhost:1234/v1",
            default_model="test-model",
        )
    )
    server, _ = server_for(store)
    await server._dispatch(  # noqa: SLF001
        "initialize",
        {
            "client_name": "test",
            "client_version": "1",
            "supported_protocol_versions": ["1.0"],
        },
    )

    created = await server._dispatch(  # noqa: SLF001
        "session/create",
        {
            "profile_id": "local",
            "workspace_path": str(tmp_path),
            "mode": "plan",
            "mcp_servers": [
                {
                    "server_id": "local-demo",
                    "display_name": "OpenCodeAgent local demo",
                    "command": "uv",
                    "arguments": ["run", "python", "examples/mcp/demo_server.py"],
                    "working_directory": str(Path(__file__).parents[3]),
                    "enabled": True,
                }
            ],
        },
    )

    assert created["available_tools"] == [
        "list_files",
        "mcp.local-demo.add_numbers",
        "mcp.local-demo.echo",
        "mcp.local-demo.server_info",
        "read_file",
        "search_code",
    ]
    await server._dispatch(  # noqa: SLF001
        "session/close", {"session_id": created["session_id"]}
    )
