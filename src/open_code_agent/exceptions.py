"""Exception hierarchy for OpenCodeAgent."""


class OpenCodeAgentError(Exception):
    """Base exception for expected OpenCodeAgent errors."""


class ConfigurationError(OpenCodeAgentError):
    """Raised when application configuration is invalid."""


class DependencyError(OpenCodeAgentError):
    """Raised when an optional external dependency is unavailable."""
