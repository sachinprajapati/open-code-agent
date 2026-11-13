"""Immutable structured command data and safe result models."""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from open_code_agent.execution.exceptions import InvalidCommandProposalError
from open_code_agent.protocol import JSONObject
from open_code_agent.workspace.paths import normalize_workspace_path

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_PROGRAM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class CommandCategory(StrEnum):
    """A conservative, explainable command category."""

    SAFE_READ = "safe_read"
    TEST = "test"
    LINT = "lint"
    FORMAT = "format"
    WORKSPACE_WRITE = "workspace_write"
    DEPENDENCY_CHANGE = "dependency_change"
    NETWORK = "network"
    DESTRUCTIVE = "destructive"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    SHELL = "shell"
    INTERPRETER = "interpreter"
    UNKNOWN = "unknown"


class CommandRisk(StrEnum):
    """Human-facing risk level; it is not an authorization decision."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class CommandProposal:
    """Normalized argv proposal bound to one workspace-relative directory."""

    executable: str
    arguments: tuple[str, ...]
    working_directory: PurePosixPath
    timeout_seconds: float
    reason: str
    environment_additions: tuple[tuple[str, str], ...] = ()
    metadata_json: str | None = None

    @classmethod
    def create(
        cls,
        *,
        executable: object,
        arguments: object = (),
        working_directory: object = ".",
        timeout_seconds: object = 120.0,
        reason: object,
        environment_additions: object = None,
        metadata: JSONObject | None = None,
    ) -> "CommandProposal":
        if (
            not isinstance(executable, str)
            or not executable
            or _CONTROL.search(executable)
        ):
            raise InvalidCommandProposalError("Command executable is invalid.")
        if not _PROGRAM.fullmatch(executable):
            raise InvalidCommandProposalError(
                "Command executable must be a program name."
            )
        if not isinstance(arguments, (list, tuple)) or not all(
            isinstance(item, str) and "\x00" not in item for item in arguments
        ):
            raise InvalidCommandProposalError(
                "Command arguments must be NUL-free strings."
            )
        if not isinstance(working_directory, str):
            raise InvalidCommandProposalError("Working directory is invalid.")
        try:
            directory = normalize_workspace_path(working_directory, allow_root=True)
        except Exception as error:
            raise InvalidCommandProposalError(
                "Working directory is invalid."
            ) from error
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise InvalidCommandProposalError("Command timeout must be positive.")
        if not isinstance(reason, str) or not reason.strip() or _CONTROL.search(reason):
            raise InvalidCommandProposalError(
                "Command reason must not be blank or control data."
            )
        additions = _normalize_environment(environment_additions)
        try:
            metadata_json = (
                json.dumps(metadata, sort_keys=True, separators=(",", ":"))
                if metadata is not None
                else None
            )
        except (TypeError, ValueError) as error:
            raise InvalidCommandProposalError(
                "Command metadata must be JSON-compatible."
            ) from error
        return cls(
            executable=executable,
            arguments=tuple(arguments),
            working_directory=directory,
            timeout_seconds=float(timeout_seconds),
            reason=reason.strip(),
            environment_additions=additions,
            metadata_json=metadata_json,
        )

    @property
    def fingerprint(self) -> str:
        """Bind approval to exact executable argv, directory, limits and additions."""
        payload = {
            "arguments": self.arguments,
            "environment_additions": self.environment_additions,
            "execution_policy_version": 1,
            "executable": self.executable,
            "timeout_seconds": self.timeout_seconds,
            "working_directory": self.working_directory.as_posix(),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def display_argv(self) -> str:
        """Return a review-only escaped representation, never an execution source."""
        return " ".join(
            _display_piece(item) for item in (self.executable, *self.arguments)
        )


@dataclass(frozen=True)
class CommandClassification:
    """The classifier's bounded explanation for a proposal."""

    category: CommandCategory
    risk: CommandRisk
    may_modify_files: bool = False
    may_use_network: bool = False
    blocked: bool = False


@dataclass(frozen=True)
class CommandExecutionResult:
    """Safe, bounded command outcome with no environment or absolute paths."""

    proposal_fingerprint: str
    executable: str
    arguments: tuple[str, ...]
    working_directory: str
    classification: CommandCategory
    risk: CommandRisk
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    combined_output_truncated: bool
    timed_out: bool
    duration_seconds: float
    approved: bool
    started: bool

    def as_content(self) -> JSONObject:
        """Return only model-safe structured execution details."""
        return {
            "proposal_fingerprint": self.proposal_fingerprint,
            "executable": self.executable,
            "arguments": list(self.arguments),
            "working_directory": self.working_directory,
            "classification": self.classification.value,
            "risk": self.risk.value,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "combined_output_truncated": self.combined_output_truncated,
            "timed_out": self.timed_out,
            "duration_seconds": self.duration_seconds,
            "approved": self.approved,
            "started": self.started,
        }


def _normalize_environment(value: object) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise InvalidCommandProposalError("Environment additions must be an object.")
    normalized: list[tuple[str, str]] = []
    for name, item in value.items():
        if not isinstance(name, str) or not _ENVIRONMENT_NAME.fullmatch(name):
            raise InvalidCommandProposalError("Environment variable name is invalid.")
        if not isinstance(item, str) or "\x00" in item:
            raise InvalidCommandProposalError("Environment variable value is invalid.")
        normalized.append((name, item))
    return tuple(sorted(normalized))


def _display_piece(value: str) -> str:
    return repr(value) if not value or any(char.isspace() for char in value) else value
