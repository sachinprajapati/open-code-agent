"""Tests for provider configuration validation results."""

import pytest
from pydantic import ValidationError

from open_code_agent.protocol import ModelError, ModelErrorCategory
from open_code_agent.providers import ConfigurationValidationResult


def test_configuration_validation_result_rules_and_json_round_trip() -> None:
    result = ConfigurationValidationResult(
        valid=False,
        error=ModelError(category=ModelErrorCategory.AUTHENTICATION, message="Denied."),
        metadata={"provider": "mock"},
    )

    restored = ConfigurationValidationResult.model_validate(
        result.model_dump(mode="json")
    )

    assert restored == result
    with pytest.raises(ValidationError, match="cannot contain an error"):
        ConfigurationValidationResult(
            valid=True,
            error=ModelError(
                category=ModelErrorCategory.UNKNOWN, message="Unexpected."
            ),
        )
    with pytest.raises(ValidationError, match="requires an error or diagnostic"):
        ConfigurationValidationResult(valid=False)
    with pytest.raises(ValidationError, match="must not be blank"):
        ConfigurationValidationResult(valid=False, diagnostic=" ")


def test_invalid_validation_result_accepts_meaningful_diagnostic() -> None:
    result = ConfigurationValidationResult(
        valid=False, diagnostic="Endpoint is unavailable."
    )

    assert result.error is None
    assert result.diagnostic == "Endpoint is unavailable."
