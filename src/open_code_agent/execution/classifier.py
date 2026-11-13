"""Deterministic, deliberately incomplete command safety classification."""

from open_code_agent.execution.models import (
    CommandCategory,
    CommandClassification,
    CommandProposal,
    CommandRisk,
)


class CommandClassifier:
    """Classify normalized argv; unknown commands never become low risk."""

    def classify(self, proposal: CommandProposal) -> CommandClassification:
        executable = proposal.executable.lower()
        if executable in {
            "sh",
            "bash",
            "zsh",
            "fish",
            "cmd",
            "cmd.exe",
            "powershell",
            "pwsh",
        }:
            return _blocked(CommandCategory.SHELL)
        if executable in {"sudo", "su", "doas"}:
            return _blocked(CommandCategory.PRIVILEGE_ESCALATION)
        if executable in {
            "rm",
            "unlink",
            "rmdir",
            "shred",
            "mkfs",
            "dd",
            "chmod",
            "chown",
        }:
            return _blocked(CommandCategory.DESTRUCTIVE)
        if executable in {
            "curl",
            "wget",
            "ssh",
            "scp",
            "sftp",
            "nc",
            "netcat",
            "telnet",
            "ftp",
        }:
            return _blocked(CommandCategory.NETWORK, network=True)
        if executable in {"python", "python3", "node", "ruby", "perl"}:
            return (
                _blocked(CommandCategory.INTERPRETER)
                if any(arg in {"-c", "-e"} for arg in proposal.arguments)
                else CommandClassification(
                    CommandCategory.INTERPRETER, CommandRisk.HIGH
                )
            )
        if executable == "git":
            return self._git(proposal.arguments)
        if executable == "pytest":
            return CommandClassification(CommandCategory.TEST, CommandRisk.MEDIUM)
        if executable == "ruff":
            if proposal.arguments[:2] == ("format", "--check"):
                return CommandClassification(CommandCategory.FORMAT, CommandRisk.LOW)
            if proposal.arguments[:1] == ("check",):
                return CommandClassification(CommandCategory.LINT, CommandRisk.LOW)
            if proposal.arguments[:1] == ("format",):
                return CommandClassification(
                    CommandCategory.WORKSPACE_WRITE,
                    CommandRisk.HIGH,
                    may_modify_files=True,
                )
        if executable in {"mypy", "pyright"}:
            return CommandClassification(CommandCategory.LINT, CommandRisk.LOW)
        if executable == "pre-commit" and proposal.arguments[:1] == ("run",):
            return CommandClassification(CommandCategory.LINT, CommandRisk.MEDIUM)
        if executable == "uv":
            return self._uv(proposal.arguments)
        if executable in {"pip", "pipx", "npm", "npx", "yarn", "pnpm"}:
            return CommandClassification(
                CommandCategory.DEPENDENCY_CHANGE,
                CommandRisk.HIGH,
                may_modify_files=True,
                may_use_network=True,
            )
        if executable == "rg":
            return CommandClassification(CommandCategory.SAFE_READ, CommandRisk.LOW)
        return CommandClassification(CommandCategory.UNKNOWN, CommandRisk.HIGH)

    @staticmethod
    def _git(arguments: tuple[str, ...]) -> CommandClassification:
        command = arguments[0] if arguments else ""
        safe = {"status", "diff", "log", "show", "rev-parse"}
        if command in safe or (
            command == "branch" and arguments[1:] == ("--show-current",)
        ):
            return CommandClassification(CommandCategory.SAFE_READ, CommandRisk.LOW)
        return _blocked(CommandCategory.DESTRUCTIVE, modifies=True)

    @staticmethod
    def _uv(arguments: tuple[str, ...]) -> CommandClassification:
        if arguments[:1] != ("run",):
            return _blocked(
                CommandCategory.DEPENDENCY_CHANGE, modifies=True, network=True
            )
        nested = arguments[1:]
        if nested[:1] == ("pytest",):
            return CommandClassification(CommandCategory.TEST, CommandRisk.MEDIUM)
        if nested[:2] == ("ruff", "check") or nested[:3] == (
            "ruff",
            "format",
            "--check",
        ):
            return CommandClassification(CommandCategory.LINT, CommandRisk.LOW)
        if nested[:1] == ("mypy",):
            return CommandClassification(CommandCategory.LINT, CommandRisk.LOW)
        return _blocked(CommandCategory.INTERPRETER)


def _blocked(
    category: CommandCategory, *, modifies: bool = False, network: bool = False
) -> CommandClassification:
    return CommandClassification(category, CommandRisk.BLOCKED, modifies, network, True)
