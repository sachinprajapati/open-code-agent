"""Safe, stable application-server failures."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AppServerError(Exception):
    """An expected failure that can be represented without implementation data."""

    code: int
    message: str


PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Reserved server-specific range.  Keep these stable for editor clients.
NOT_INITIALIZED = -32001
ALREADY_INITIALIZED = -32002
INCOMPATIBLE_PROTOCOL = -32003
PROFILE_NOT_FOUND = -32004
INVALID_WORKSPACE = -32005
SESSION_NOT_FOUND = -32006
SESSION_BUSY = -32007
RUN_NOT_FOUND = -32008
APPROVAL_FAILED = -32009
PROVIDER_FAILURE = -32010
AGENT_LIMIT_REACHED = -32011
SERVER_SHUTTING_DOWN = -32012
RESOURCE_LIMIT = -32013
