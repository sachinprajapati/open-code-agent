"""HTTP implementation of the OpenAI-compatible Chat Completions provider."""

import asyncio
import json
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Self

import httpx

from open_code_agent.protocol import (
    ModelCapabilities,
    ModelError,
    ModelErrorCategory,
    ModelInfo,
    ModelRequest,
    ModelStreamEvent,
    StreamError,
)
from open_code_agent.providers.base import ConfigurationValidationResult
from open_code_agent.providers.exceptions import ProviderError
from open_code_agent.providers.openai_compatible.errors import (
    OpenAICompatibleMappingError,
    OpenAICompatibleRequestError,
    OpenAICompatibleStreamError,
)
from open_code_agent.providers.openai_compatible.mapper import (
    OpenAICompatibleStreamMapper,
    map_chat_completions_request,
)
from open_code_agent.providers.openai_compatible.streaming import SSEEventParser
from open_code_agent.providers.profiles import OpenAICompatibleProfile
from open_code_agent.providers.secrets import (
    EnvironmentSecretResolver,
    SecretResolutionError,
    SecretResolver,
)

_SAFE_ERROR_MESSAGE_LIMIT = 512


class OpenAICompatibleProvider:
    """One configured OpenAI-compatible Chat Completions provider profile.

    The provider owns and closes clients it creates from a transport or default
    settings. An injected ``httpx.AsyncClient`` remains caller-owned and is not
    closed by :meth:`aclose`.
    """

    def __init__(
        self,
        profile: OpenAICompatibleProfile,
        *,
        secret_resolver: SecretResolver | None = None,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        native_tool_calling: bool = False,
        max_sse_event_size: int = 1_048_576,
    ) -> None:
        """Configure a profile with injected secret and HTTP dependencies."""
        if client is not None and transport is not None:
            raise ValueError("Provide either client or transport, not both.")
        if max_sse_event_size <= 0:
            raise ValueError("max_sse_event_size must be positive.")
        self._profile = profile
        self._secret_resolver = secret_resolver or EnvironmentSecretResolver()
        self._native_tool_calling = native_tool_calling
        self._max_sse_event_size = max_sse_event_size
        self._closed = False
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(
                profile.request_timeout_seconds,
                connect=profile.connect_timeout_seconds,
            ),
        )

    @property
    def provider_id(self) -> str:
        """Return the stable registry ID namespaced by profile ID."""
        return f"openai-compatible.{self._profile.profile_id}"

    @property
    def display_name(self) -> str:
        """Return the profile's human-readable display name."""
        return self._profile.display_name

    async def __aenter__(self) -> Self:
        """Enter the provider lifecycle without opening additional resources."""
        self._ensure_open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close provider-owned resources when the context exits."""
        await self.aclose()

    async def aclose(self) -> None:
        """Close an owned client and prevent further provider use."""
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        """Return a fresh normalized streaming response iterator."""
        return self._stream(request)

    async def _stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        self._ensure_open()
        try:
            payload = map_chat_completions_request(request)
            headers = await self._headers(accept="text/event-stream")
            mapper = OpenAICompatibleStreamMapper(self.provider_id)
            parser = SSEEventParser(self._max_sse_event_size)
            async with self._client.stream(
                "POST",
                self._profile.endpoint("chat/completions"),
                json=payload,
                headers=headers,
            ) as response:
                if not response.is_success:
                    yield StreamError(
                        error=await self._response_error(
                            response,
                            not_found_is_model=True,
                            sensitive_values=self._sensitive_values(headers),
                        )
                    )
                    return
                async for chunk in response.aiter_bytes():
                    for data in parser.feed(chunk):
                        if data == "[DONE]":
                            for event in mapper.finish():
                                yield event
                            return
                        for event in mapper.consume(data):
                            yield event
                        if mapper.is_terminal:
                            return
                for data in parser.finalize():
                    if data == "[DONE]":
                        for event in mapper.finish():
                            yield event
                        return
                    for event in mapper.consume(data):
                        yield event
                    if mapper.is_terminal:
                        return
                for event in mapper.finish():
                    yield event
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException:
            yield StreamError(
                error=self._local_error(
                    ModelErrorCategory.TIMEOUT, "Request timed out.", True
                )
            )
        except httpx.RequestError:
            yield StreamError(
                error=self._local_error(
                    ModelErrorCategory.CONNECTION, "Connection failed.", True
                )
            )
        except OpenAICompatibleStreamError as error:
            yield StreamError(
                error=self._local_error(
                    ModelErrorCategory.PROVIDER_ERROR, str(error), False
                )
            )
        except SecretResolutionError as error:
            yield StreamError(
                error=self._local_error(
                    ModelErrorCategory.AUTHENTICATION, str(error), False
                )
            )
        except OpenAICompatibleMappingError as error:
            yield StreamError(
                error=self._local_error(
                    ModelErrorCategory.INVALID_REQUEST, str(error), False
                )
            )

    async def list_models(self) -> tuple[ModelInfo, ...]:
        """Fetch and conservatively map the configured endpoint's models list."""
        self._ensure_open()
        try:
            headers = await self._headers(accept="application/json")
            response = await self._client.get(
                self._profile.endpoint("models"),
                headers=headers,
            )
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException as error:
            raise OpenAICompatibleRequestError(
                self._local_error(
                    ModelErrorCategory.TIMEOUT, "Model listing timed out.", True
                )
            ) from error
        except httpx.RequestError as error:
            raise OpenAICompatibleRequestError(
                self._local_error(
                    ModelErrorCategory.CONNECTION,
                    "Model listing connection failed.",
                    True,
                )
            ) from error
        if not response.is_success:
            raise OpenAICompatibleRequestError(
                await self._response_error(
                    response,
                    not_found_is_model=False,
                    sensitive_values=self._sensitive_values(headers),
                )
            )
        return self._map_models_response(response)

    async def validate_configuration(self) -> ConfigurationValidationResult:
        """Use the lightweight models endpoint for structured validation."""
        try:
            models = await self.list_models()
        except OpenAICompatibleRequestError as error:
            if error.model_error.http_status == 404:
                return ConfigurationValidationResult(
                    valid=False,
                    error=error.model_error,
                    diagnostic=(
                        "Configured server does not provide the required "
                        "/models endpoint."
                    ),
                )
            return ConfigurationValidationResult(valid=False, error=error.model_error)
        except SecretResolutionError as error:
            return ConfigurationValidationResult(valid=False, diagnostic=str(error))
        except (OpenAICompatibleMappingError, ProviderError) as error:
            return ConfigurationValidationResult(valid=False, diagnostic=str(error))
        return ConfigurationValidationResult(
            valid=True, metadata={"model_count": len(models)}
        )

    async def _headers(self, *, accept: str) -> dict[str, str]:
        """Build request headers immediately before a request resolves secrets."""
        headers = {"Accept": accept, "Content-Type": "application/json"}
        headers.update(self._profile.extra_headers)
        if self._profile.api_key_secret_ref is not None:
            secret = await self._secret_resolver.resolve(
                self._profile.api_key_secret_ref
            )
            headers["Authorization"] = f"Bearer {secret}"
        if self._profile.organization is not None:
            headers["OpenAI-Organization"] = self._profile.organization
        if self._profile.project is not None:
            headers["OpenAI-Project"] = self._profile.project
        return headers

    def _map_models_response(self, response: httpx.Response) -> tuple[ModelInfo, ...]:
        try:
            decoded: object = response.json()
        except json.JSONDecodeError as error:
            raise OpenAICompatibleMappingError(
                "Models endpoint returned malformed JSON."
            ) from error
        if not isinstance(decoded, dict):
            raise OpenAICompatibleMappingError(
                "Models endpoint response must be an object."
            )
        data = decoded.get("data")
        if not isinstance(data, list):
            raise OpenAICompatibleMappingError(
                "Models endpoint response must contain a data array."
            )
        models: list[ModelInfo] = []
        for item in data:
            if not isinstance(item, dict):
                raise OpenAICompatibleMappingError(
                    "Models endpoint data items must be objects."
                )
            model_id = item.get("id")
            if not isinstance(model_id, str) or not model_id.strip():
                raise OpenAICompatibleMappingError(
                    "Models endpoint data items require a non-empty id."
                )
            models.append(
                ModelInfo(
                    id=model_id,
                    display_name=model_id,
                    provider=self.provider_id,
                    capabilities=ModelCapabilities(
                        native_tool_calling=self._native_tool_calling
                    ),
                )
            )
        return tuple(sorted(models, key=lambda model: model.id))

    async def _response_error(
        self,
        response: httpx.Response,
        *,
        not_found_is_model: bool,
        sensitive_values: tuple[str, ...] = (),
    ) -> ModelError:
        """Extract bounded safe error data without retaining response secrets."""
        body = await response.aread()
        message, provider_code = self._safe_error_details(body, sensitive_values)
        status = response.status_code
        category = self._status_category(status, not_found_is_model)
        return ModelError(
            category=category,
            message=message or f"Provider returned HTTP {status}.",
            retryable=status == 429 or 500 <= status <= 599,
            provider=self.provider_id,
            provider_error_code=provider_code,
            http_status=status,
            retry_after_seconds=self._retry_after(response.headers.get("Retry-After")),
        )

    def _local_error(
        self, category: ModelErrorCategory, message: str, retryable: bool
    ) -> ModelError:
        return ModelError(
            category=category,
            message=message,
            retryable=retryable,
            provider=self.provider_id,
        )

    @staticmethod
    def _safe_error_details(
        body: bytes, sensitive_values: tuple[str, ...]
    ) -> tuple[str | None, str | None]:
        """Read common JSON error shapes while bounding and sanitizing text."""
        text = body[:_SAFE_ERROR_MESSAGE_LIMIT].decode("utf-8", errors="replace")
        try:
            decoded: object = json.loads(text)
        except json.JSONDecodeError:
            return None, None
        if not isinstance(decoded, dict):
            return None, None
        error = decoded.get("error")
        if not isinstance(error, dict):
            return None, None
        message = error.get("message")
        code = error.get("code")
        safe_message = (
            OpenAICompatibleProvider._sanitize_message(message)
            if isinstance(message, str)
            else None
        )
        safe_code = (
            OpenAICompatibleProvider._sanitize_message(code, sensitive_values)
            if isinstance(code, str)
            else None
        )
        return (
            OpenAICompatibleProvider._redact_sensitive(safe_message, sensitive_values),
            safe_code,
        )

    @staticmethod
    def _sanitize_message(value: str, sensitive_values: tuple[str, ...] = ()) -> str:
        sanitized = " ".join(value.split())
        return (
            OpenAICompatibleProvider._redact_sensitive(
                sanitized[:_SAFE_ERROR_MESSAGE_LIMIT], sensitive_values
            )
            or ""
        )

    @staticmethod
    def _redact_sensitive(
        value: str | None, sensitive_values: tuple[str, ...]
    ) -> str | None:
        if value is None:
            return None
        redacted = value
        for sensitive_value in sensitive_values:
            if sensitive_value:
                redacted = redacted.replace(sensitive_value, "[redacted]")
        return redacted

    @staticmethod
    def _sensitive_values(headers: dict[str, str]) -> tuple[str, ...]:
        authorization = headers.get("Authorization")
        if authorization is None:
            return ()
        return (authorization.removeprefix("Bearer "),)

    @staticmethod
    def _retry_after(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            seconds = float(value)
        except ValueError:
            return None
        return seconds if seconds >= 0 else None

    @staticmethod
    def _status_category(status: int, not_found_is_model: bool) -> ModelErrorCategory:
        if status in {400, 422}:
            return ModelErrorCategory.INVALID_REQUEST
        if status == 401:
            return ModelErrorCategory.AUTHENTICATION
        if status == 403:
            return ModelErrorCategory.PERMISSION
        if status == 404:
            return (
                ModelErrorCategory.MODEL_NOT_FOUND
                if not_found_is_model
                else ModelErrorCategory.PROVIDER_ERROR
            )
        if status == 408:
            return ModelErrorCategory.TIMEOUT
        if status == 429:
            return ModelErrorCategory.RATE_LIMIT
        return ModelErrorCategory.PROVIDER_ERROR

    def _ensure_open(self) -> None:
        if self._closed or self._client.is_closed:
            raise ProviderError("OpenAI-compatible provider is closed.")
