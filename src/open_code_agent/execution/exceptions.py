"""Expected failures at the approval-gated command boundary."""

from open_code_agent.exceptions import OpenCodeAgentError


class CommandExecutionError(OpenCodeAgentError):
    """Base error for validated command execution."""


class InvalidCommandProposalError(CommandExecutionError):
    """Raised when untrusted structured command data is invalid."""


class CommandBlockedError(CommandExecutionError):
    """Raised when policy blocks a command before approval."""


class CommandApprovalError(CommandExecutionError):
    """Raised when approval is unavailable, denied, or no longer bound."""
