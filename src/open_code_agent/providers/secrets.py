"""Asynchronous secret-reference resolution for provider requests."""

import os
import re
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from open_code_agent.exceptions import OpenCodeAgentError

_ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SecretResolutionError(OpenCodeAgentError):
    """Base exception for secret-reference resolution failures."""


class InvalidSecretReferenceError(SecretResolutionError):
    """Raised when a resolver cannot safely interpret a secret reference."""


class SecretNotFoundError(SecretResolutionError):
    """Raised when a reference is missing or resolves to an empty secret."""


@runtime_checkable
class SecretResolver(Protocol):
    """Resolve a secret reference without exposing secret storage details."""

    async def resolve(self, secret_ref: str) -> str:
        """Resolve and return a non-empty secret value."""


class EnvironmentSecretResolver:
    """Resolve explicit ``env:VARIABLE_NAME`` references at request time."""

    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        """Use an injected mapping, or process environment at construction time."""
        self._environment: Mapping[str, str] = (
            os.environ if environment is None else dict(environment)
        )

    async def resolve(self, secret_ref: str) -> str:
        """Resolve an environment reference without revealing its value in errors."""
        name = self._environment_name(secret_ref)
        value = self._environment.get(name)
        if value is None or not value:
            raise SecretNotFoundError(
                f"Secret reference '{secret_ref}' is unavailable."
            )
        return value

    @staticmethod
    def _environment_name(secret_ref: str) -> str:
        """Validate an explicit environment-variable reference."""
        prefix = "env:"
        if not secret_ref.startswith(prefix):
            raise InvalidSecretReferenceError(
                "Only explicit env: secret references are supported."
            )
        name = secret_ref.removeprefix(prefix)
        if not _ENVIRONMENT_NAME_PATTERN.fullmatch(name):
            raise InvalidSecretReferenceError(
                "Secret reference contains an invalid name."
            )
        return name


class MappingSecretResolver:
    """In-memory resolver for tests and future host-provided secret stores."""

    def __init__(self, secrets: Mapping[str, str]) -> None:
        """Copy supplied secrets without exposing a mutable internal mapping."""
        self._secrets = dict(secrets)

    async def resolve(self, secret_ref: str) -> str:
        """Resolve a configured reference, never including its value in an error."""
        value = self._secrets.get(secret_ref)
        if value is None or not value:
            raise SecretNotFoundError(
                f"Secret reference '{secret_ref}' is unavailable."
            )
        return value
