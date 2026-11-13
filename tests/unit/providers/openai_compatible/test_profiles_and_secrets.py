"""Tests for OpenAI-compatible profiles and secret resolvers."""

import pytest
from pydantic import ValidationError

from open_code_agent.providers import (
    EnvironmentSecretResolver,
    MappingSecretResolver,
    OpenAICompatibleProfile,
)
from open_code_agent.providers.secrets import (
    InvalidSecretReferenceError,
    SecretNotFoundError,
)


def profile(**overrides: object) -> OpenAICompatibleProfile:
    """Build a valid default profile with narrowly scoped overrides."""
    values: dict[str, object] = {
        "profile_id": "example",
        "display_name": "Example endpoint",
        "base_url": "https://api.example.test/v1/",
        "default_model": "example-model",
    }
    values.update(overrides)
    return OpenAICompatibleProfile.model_validate(values)


def test_profile_validates_urls_timeouts_headers_and_endpoints() -> None:
    configured = profile(extra_headers={"X-Client": "OpenCodeAgent"})

    assert configured.base_url == "https://api.example.test/v1"
    assert configured.endpoint("/models") == "https://api.example.test/v1/models"
    assert configured.endpoint("chat/completions") == (
        "https://api.example.test/v1/chat/completions"
    )
    assert profile(base_url="http://localhost:11434/v1").base_url.startswith("http:")
    assert profile(base_url="http://127.0.0.1:1234/v1").base_url.startswith("http:")
    with pytest.raises(ValidationError, match="allow_insecure_http"):
        profile(base_url="http://remote.example.test/v1")
    assert profile(
        base_url="http://remote.example.test/v1", allow_insecure_http=True
    ).base_url.startswith("http:")
    for unsafe_url in (
        "https://user:password@example.test/v1",
        "https://example.test/v1?key=value",
        "https://example.test/v1#fragment",
    ):
        with pytest.raises(ValidationError):
            profile(base_url=unsafe_url)
    with pytest.raises(ValidationError):
        profile(request_timeout_seconds=0)
    with pytest.raises(ValidationError):
        profile(connect_timeout_seconds=-1)
    with pytest.raises(ValidationError, match="managed"):
        profile(extra_headers={"Authorization": "Bearer forbidden"})
    with pytest.raises(ValidationError, match="newline"):
        profile(extra_headers={"X-Test": "safe\r\nInjected: no"})
    with pytest.raises(ValidationError, match="duplicates"):
        profile(extra_headers={"X-Test": "one", "x-test": "two"})


@pytest.mark.asyncio
async def test_secret_resolvers_are_injected_redacted_and_copy_input() -> None:
    environment = {"TEST_API_KEY": "secret-value"}
    environment_resolver = EnvironmentSecretResolver(environment)
    mapping = {"host:api-key": "mapped-secret"}
    mapping_resolver = MappingSecretResolver(mapping)
    mapping["host:api-key"] = "mutated"

    assert await environment_resolver.resolve("env:TEST_API_KEY") == "secret-value"
    assert await mapping_resolver.resolve("host:api-key") == "mapped-secret"
    with pytest.raises(SecretNotFoundError) as missing:
        await environment_resolver.resolve("env:MISSING_KEY")
    assert "secret-value" not in str(missing.value)
    with pytest.raises(SecretNotFoundError) as empty:
        await EnvironmentSecretResolver({"EMPTY_KEY": ""}).resolve("env:EMPTY_KEY")
    assert "EMPTY_KEY" in str(empty.value)
    with pytest.raises(InvalidSecretReferenceError):
        await environment_resolver.resolve("file:secrets.txt")
    with pytest.raises(InvalidSecretReferenceError):
        await environment_resolver.resolve("env:NOT-VALID")
