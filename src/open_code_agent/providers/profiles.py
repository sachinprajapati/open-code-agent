"""Typed provider profiles that store references to, never values of, secrets."""

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator

from open_code_agent.protocol import JSONObject
from open_code_agent.protocol.common import ProtocolModel

_PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_MANAGED_HEADERS = {"authorization", "openai-organization", "openai-project"}


def _is_loopback_host(hostname: str) -> bool:
    """Return whether a hostname is an explicit loopback host without DNS."""
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


class OpenAICompatibleProfile(ProtocolModel):
    """Configuration for one OpenAI-compatible Chat Completions endpoint.

    ``api_key_secret_ref`` identifies a secret for a resolver; it never stores
    the secret value. Allowing non-loopback HTTP can expose prompts and API keys
    in transit and must be explicitly opted into.
    """

    profile_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    display_name: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    default_model: str = Field(min_length=1)
    api_key_secret_ref: str | None = None
    request_timeout_seconds: float = Field(default=60.0, gt=0)
    connect_timeout_seconds: float = Field(default=10.0, gt=0)
    allow_insecure_http: bool = False
    organization: str | None = None
    project: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)
    metadata: JSONObject | None = None

    @field_validator("profile_id")
    @classmethod
    def profile_id_must_be_portable(cls, value: str) -> str:
        """Reject profile IDs outside the registry-compatible portable format."""
        if not _PROFILE_ID_PATTERN.fullmatch(value):
            raise ValueError("profile_id must be a lowercase portable identifier.")
        return value

    @field_validator("display_name", "default_model")
    @classmethod
    def required_strings_must_not_be_blank(cls, value: str) -> str:
        """Reject whitespace-only identifying values."""
        if not value.strip():
            raise ValueError(
                "Profile display_name and default_model must not be blank."
            )
        return value

    @field_validator("api_key_secret_ref", "organization", "project")
    @classmethod
    def optional_strings_must_not_be_blank(cls, value: str | None) -> str | None:
        """Reject supplied optional values that contain only whitespace."""
        if value is not None and not value.strip():
            raise ValueError(
                "Optional profile strings must not be blank when supplied."
            )
        return value

    @field_validator("base_url")
    @classmethod
    def base_url_must_be_safe_http_url(cls, value: str) -> str:
        """Validate and normalize an endpoint URL without resolving its host."""
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute HTTP or HTTPS URL.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(
                "base_url must not contain username or password credentials."
            )
        if parsed.query:
            raise ValueError("base_url must not contain a query string.")
        if parsed.fragment:
            raise ValueError("base_url must not contain a fragment.")
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
        )

    @field_validator("extra_headers")
    @classmethod
    def extra_headers_must_be_safe(cls, value: dict[str, str]) -> dict[str, str]:
        """Allow only non-secret, non-managed, injection-safe extra headers."""
        normalized_names: set[str] = set()
        for name, header_value in value.items():
            normalized_name = name.lower()
            if not name.strip() or not header_value.strip():
                raise ValueError("Extra header names and values must not be blank.")
            if (
                "\r" in name
                or "\n" in name
                or "\r" in header_value
                or "\n" in header_value
            ):
                raise ValueError("Extra headers must not contain newline characters.")
            if normalized_name in _MANAGED_HEADERS:
                raise ValueError(f"Extra header '{name}' is managed by the provider.")
            if normalized_name in normalized_names:
                raise ValueError(
                    "Extra headers must not contain case-insensitive duplicates."
                )
            normalized_names.add(normalized_name)
        return dict(value)

    @model_validator(mode="after")
    def insecure_http_requires_opt_in(self) -> "OpenAICompatibleProfile":
        """Require explicit consent for plaintext non-loopback connections."""
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme == "http"
            and not _is_loopback_host(parsed.hostname or "")
            and not self.allow_insecure_http
        ):
            raise ValueError(
                "Non-loopback HTTP requires allow_insecure_http=True because "
                "it can expose secrets."
            )
        return self

    def endpoint(self, path: str) -> str:
        """Build an API endpoint without duplicate slashes or implicit version paths."""
        return f"{self.base_url}/{path.lstrip('/')}"
