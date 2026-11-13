"""Expected local errors while assembling or collecting an agent run."""

from open_code_agent.exceptions import OpenCodeAgentError


class AgentError(OpenCodeAgentError):
    """Base exception for agent-loop infrastructure errors."""


class AgentAssemblyError(AgentError):
    """Raised when normalized provider events violate their stream contract."""
