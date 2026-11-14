"""Resource bounds for the stdio application server."""

from pydantic import Field

from open_code_agent.protocol.common import ProtocolModel


class AppServerLimits(ProtocolModel):
    """Finite per-connection limits; values are deliberately conservative."""

    max_input_message_bytes: int = Field(default=1_048_576, gt=0)
    max_output_message_bytes: int = Field(default=1_048_576, gt=0)
    max_sessions: int = Field(default=16, gt=0)
    max_pending_client_requests: int = Field(default=32, gt=0)
    max_pending_server_requests: int = Field(default=16, gt=0)
    max_concurrent_runs: int = Field(default=4, gt=0)
    max_event_payload_bytes: int = Field(default=65_536, gt=0)
    approval_timeout_seconds: float = Field(default=120.0, gt=0)
    shutdown_timeout_seconds: float = Field(default=10.0, gt=0)
