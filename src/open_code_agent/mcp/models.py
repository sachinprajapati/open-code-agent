"""Validated, secret-free configuration for local MCP servers."""
# ruff: noqa: E501

import re
from enum import StrEnum

from pydantic import Field, field_validator

from open_code_agent.protocol.common import ProtocolModel

_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ENV = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SHELLS = frozenset({"sh", "bash", "zsh", "fish", "cmd", "powershell", "pwsh"})


class MCPTransportKind(StrEnum):
    """Transport kinds supported by this version."""

    STDIO = "stdio"


class MCPServerConfig(ProtocolModel):
    """Persistable server configuration. Secret values are never part of it."""

    server_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    transport: MCPTransportKind = MCPTransportKind.STDIO
    command: str = Field(min_length=1, max_length=1024)
    arguments: list[str] = Field(default_factory=list, max_length=128)
    working_directory: str | None = Field(default=None, max_length=4096)
    environment_secret_refs: dict[str, str] = Field(default_factory=dict)
    enabled: bool = False
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    @field_validator("transport", mode="before")
    @classmethod
    def valid_transport(cls, value: object) -> MCPTransportKind:
        if value == MCPTransportKind.STDIO or value == MCPTransportKind.STDIO.value:
            return MCPTransportKind.STDIO
        raise ValueError("only stdio transport is supported.")

    @field_validator("server_id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError("server_id must be lowercase and URL-safe.")
        return value

    @field_validator("display_name", "command")
    @classmethod
    def safe_string(cls, value: str) -> str:
        if not value.strip() or _CONTROL.search(value):
            raise ValueError("value must be non-blank and contain no control characters.")
        return value

    @field_validator("command")
    @classmethod
    def command_must_not_be_a_shell(cls, value: str) -> str:
        """MCP startup always uses exec argv and never a shell interpreter."""
        if value.rsplit("/", 1)[-1].lower() in _SHELLS:
            raise ValueError("shell interpreters are not valid MCP commands.")
        return value

    @field_validator("arguments")
    @classmethod
    def safe_arguments(cls, value: list[str]) -> list[str]:
        if any(not item or _CONTROL.search(item) for item in value):
            raise ValueError("arguments must be non-empty control-free strings.")
        return value

    @field_validator("working_directory")
    @classmethod
    def safe_cwd(cls, value: str | None) -> str | None:
        if value is not None and (not value or _CONTROL.search(value)):
            raise ValueError("working directory is invalid.")
        return value

    @field_validator("environment_secret_refs")
    @classmethod
    def safe_environment(cls, value: dict[str, str]) -> dict[str, str]:
        for name, reference in value.items():
            if not _ENV.fullmatch(name) or not reference.startswith("vscode:"):
                raise ValueError("environment must use names and opaque vscode: references.")
        return value


class MCPConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
