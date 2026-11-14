"""Deterministic checked-in protocol manifest generation."""

from __future__ import annotations

import json
from pathlib import Path

from open_code_agent.app_server.server import _STATEFUL_METHODS, PROTOCOL_VERSION


def protocol_manifest() -> dict[str, object]:
    """Return the stable public surface used by a future generated client."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "transport": {"kind": "stdio", "framing": "utf-8-json-lines"},
        "methods": [
            {"name": "initialize", "direction": "client_to_server"},
            *[
                {"name": name, "direction": "client_to_server"}
                for name in sorted(_STATEFUL_METHODS)
            ],
            {"name": "approval/request", "direction": "server_to_client"},
        ],
        "notifications": [
            {"name": "initialized", "direction": "client_to_server"},
            {"name": "exit", "direction": "client_to_server"},
            {"name": "agent/event", "direction": "server_to_client"},
        ],
        "capabilities": [
            "profiles",
            "sessions",
            "streaming_events",
            "bidirectional_approvals",
            "cancellation",
        ],
    }


def render_manifest() -> str:
    """Render manifest reproducibly for source control."""
    return json.dumps(protocol_manifest(), indent=2, sort_keys=True) + "\n"


def write_manifest(path: Path) -> None:
    """Write the checked-in file; callers choose when a mutation is desired."""
    path.write_text(render_manifest(), encoding="utf-8")


if __name__ == "__main__":
    write_manifest(Path("docs/app-server.protocol.json"))
