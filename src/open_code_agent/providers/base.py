"""Provider-neutral asynchronous provider contract."""

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from pydantic import Field, field_validator, model_validator

from open_code_agent.protocol import (
    JSONObject,
    ModelError,
    ModelInfo,
    ModelRequest,
    ModelStreamEvent,
)
from open_code_agent.protocol.common import ProtocolModel


class ConfigurationValidationResult(ProtocolModel):
    """The outcome of validating provider configuration.

    Invalid credentials and other ordinary configuration failures are returned
    as data rather than raised. Unexpected local programming failures may still
    raise normal Python exceptions.
    """

    valid: bool
    error: ModelError | None = None
    diagnostic: str | None = None
    metadata: JSONObject = Field(default_factory=dict)

    @field_validator("diagnostic")
    @classmethod
    def diagnostic_must_not_be_blank(cls, value: str | None) -> str | None:
        """Reject supplied diagnostics that contain only whitespace."""
        if value is not None and not value.strip():
            raise ValueError("Diagnostic must not be blank when supplied.")
        return value

    @model_validator(mode="after")
    def validate_result_state(self) -> "ConfigurationValidationResult":
        """Keep valid and invalid configuration results unambiguous."""
        if self.valid and self.error is not None:
            raise ValueError("A valid configuration result cannot contain an error.")
        if not self.valid and self.error is None and self.diagnostic is None:
            raise ValueError(
                "An invalid configuration result requires an error or diagnostic."
            )
        return self


@runtime_checkable
class ModelProvider(Protocol):
    """Read-only identity and asynchronous streaming contract for providers.

    ``provider_id`` identifies an adapter implementation, while model IDs in
    :class:`~open_code_agent.protocol.ModelRequest` identify models offered by
    that provider. Cancelling the task consuming ``stream`` cancels the normal
    asynchronous iteration; implementations must not convert cancellation into
    a normalized stream event.
    """

    @property
    def provider_id(self) -> str:
        """Return the stable provider implementation identifier."""

    @property
    def display_name(self) -> str:
        """Return a human-readable name for the provider."""

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        """Yield normalized response events for a request."""

    async def list_models(self) -> tuple[ModelInfo, ...]:
        """Return the provider's currently configured model records."""

    async def validate_configuration(self) -> ConfigurationValidationResult:
        """Return structured configuration validation data."""
