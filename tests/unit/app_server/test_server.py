from pathlib import Path

from open_code_agent.app_server.limits import AppServerLimits
from open_code_agent.app_server.server import AppServer
from open_code_agent.app_server.transport import AsyncLineTransport
from open_code_agent.persistence import ProviderProfileStore
from open_code_agent.providers import OpenAICompatibleProfile


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
