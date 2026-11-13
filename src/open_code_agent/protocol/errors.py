"""Serializable, normalized provider error data."""

from enum import StrEnum

from pydantic import Field, field_validator

from open_code_agent.protocol.common import JSONObject, ProtocolModel


class ModelErrorCategory(StrEnum):
    """Provider-neutral error classifications."""

    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    INVALID_REQUEST = "invalid_request"
    MODEL_NOT_FOUND = "model_not_found"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    CONTEXT_LENGTH = "context_length"
    CONTENT_FILTER = "content_filter"
    PROVIDER_ERROR = "provider_error"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ModelError(ProtocolModel):
    """A serializable error returned by a future provider adapter."""

    category: ModelErrorCategory = Field(strict=False)
    message: str = Field(min_length=1)
    retryable: bool = False
    provider: str | None = None
    provider_error_code: str | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    retry_after_seconds: float | None = Field(default=None, ge=0)
    metadata: JSONObject = Field(default_factory=dict)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        """Reject messages that contain only whitespace."""
        if not value.strip():
            raise ValueError("Model error message must not be blank.")
        return value
