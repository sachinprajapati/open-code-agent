"""Conservative execution policy and controlled child environment."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from open_code_agent.execution.exceptions import CommandBlockedError
from open_code_agent.execution.models import (
    CommandCategory,
    CommandClassification,
    CommandProposal,
)

_DEFAULT_DENIED = frozenset(
    {
        "bash",
        "cmd",
        "cmd.exe",
        "curl",
        "doas",
        "fish",
        "ftp",
        "nc",
        "netcat",
        "powershell",
        "pwsh",
        "scp",
        "sftp",
        "sh",
        "ssh",
        "su",
        "sudo",
        "telnet",
        "wget",
        "zsh",
    }
)


@dataclass(frozen=True)
class CommandExecutionPolicy:
    """Application-owned controls; model input cannot relax these fields."""

    execution_enabled: bool = False
    max_timeout_seconds: float = 120.0
    max_stdout_bytes: int = 65_536
    max_stderr_bytes: int = 65_536
    max_combined_output_bytes: int = 98_304
    max_line_length: int = 4_000
    allowed_environment_variables: frozenset[str] = frozenset()
    inherited_environment_variables: tuple[str, ...] = (
        "PATH",
        "HOME",
        "TMPDIR",
        "TEMP",
        "TMP",
    )
    allowed_executables: frozenset[str] | None = None
    denied_executables: frozenset[str] = _DEFAULT_DENIED
    allow_network_commands: bool = False
    allow_interpreters: bool = False
    allow_package_installers: bool = False
    allow_workspace_writes: bool = False
    allow_git_mutations: bool = False
    allow_shells: bool = False
    approval_required: bool = True
    version: int = 1
    termination_grace_seconds: float = 1.0

    def __post_init__(self) -> None:
        if (
            self.max_timeout_seconds <= 0
            or self.max_stdout_bytes <= 0
            or self.max_stderr_bytes <= 0
        ):
            raise ValueError("Execution limits must be positive.")
        if self.max_combined_output_bytes <= 0 or self.max_line_length <= 0:
            raise ValueError("Execution limits must be positive.")

    def validate(
        self, proposal: CommandProposal, classification: CommandClassification
    ) -> None:
        """Fail closed before approval or process creation."""
        if not self.execution_enabled:
            raise CommandBlockedError("Command execution is disabled by policy.")
        if proposal.timeout_seconds > self.max_timeout_seconds:
            raise CommandBlockedError("Command timeout exceeds the execution policy.")
        if proposal.executable in self.denied_executables:
            raise CommandBlockedError("Command executable is blocked by policy.")
        if (
            self.allowed_executables is not None
            and proposal.executable not in self.allowed_executables
        ):
            raise CommandBlockedError("Command executable is not allowed by policy.")
        if classification.blocked:
            raise CommandBlockedError("Command is blocked by the execution policy.")
        if classification.may_use_network and not self.allow_network_commands:
            raise CommandBlockedError("Network-capable commands are blocked by policy.")
        if (
            classification.category is CommandCategory.INTERPRETER
            and not self.allow_interpreters
        ):
            raise CommandBlockedError("Interpreter commands are blocked by policy.")
        if (
            classification.category is CommandCategory.DEPENDENCY_CHANGE
            and not self.allow_package_installers
        ):
            raise CommandBlockedError("Package changes are blocked by policy.")
        if (
            classification.category is CommandCategory.WORKSPACE_WRITE
            and not self.allow_workspace_writes
        ):
            raise CommandBlockedError(
                "Workspace-mutating commands are blocked by policy."
            )
        if any(
            name not in self.allowed_environment_variables
            for name, _ in proposal.environment_additions
        ):
            raise CommandBlockedError("Command environment additions are not allowed.")
        protected = set(self.inherited_environment_variables)
        if any(name in protected for name, _ in proposal.environment_additions):
            raise CommandBlockedError(
                "Command cannot override protected environment variables."
            )


def build_child_environment(
    policy: CommandExecutionPolicy,
    proposal: CommandProposal,
    parent_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a minimal allowlisted environment without secret inheritance."""
    parent = os.environ if parent_environment is None else parent_environment
    inherited = list(policy.inherited_environment_variables)
    if os.name == "nt":
        inherited.extend(["SYSTEMROOT", "WINDIR"])
    environment = {name: parent[name] for name in inherited if name in parent}
    environment.update(dict(proposal.environment_additions))
    return environment
