"""Durable, secret-safe OpenAI-compatible provider profiles.

The JSON file is deliberately a small single-user store.  A per-instance lock
prevents concurrent callers of the same store from interleaving read/modify/
write operations.  It does not provide cross-process locking; two processes
writing the same file concurrently are unsupported.
"""

import contextlib
import json
import os
import stat
import tempfile
import threading
from pathlib import Path

from pydantic import ValidationError

from open_code_agent.exceptions import ConfigurationError
from open_code_agent.providers import OpenAICompatibleProfile

_FORMAT_VERSION = 1


class ProfileStoreError(ConfigurationError):
    """Raised for a safe, actionable profile-store configuration failure."""


class ProfileNotFoundError(ProfileStoreError):
    """Raised when a requested provider profile is not stored."""


class DuplicateProfileError(ProfileStoreError):
    """Raised when an add would overwrite an existing profile."""


class ProviderProfileStore:
    """Versioned local profile store that persists references, never secrets."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        """Return the configured path without creating it or its parents."""
        return self._path

    def list(self) -> tuple[OpenAICompatibleProfile, ...]:
        """Return profiles in stable profile-ID order."""
        with self._lock:
            _, profiles = self._load()
            return tuple(
                profiles[key].model_copy(deep=True) for key in sorted(profiles)
            )

    def get(self, profile_id: str) -> OpenAICompatibleProfile:
        """Return one detached validated profile."""
        with self._lock:
            _, profiles = self._load()
            try:
                return profiles[profile_id].model_copy(deep=True)
            except KeyError as error:
                raise ProfileNotFoundError(
                    f"Profile '{profile_id}' was not found."
                ) from error

    def add(self, profile: OpenAICompatibleProfile) -> None:
        """Persist a new profile, rejecting accidental overwrite."""
        with self._lock:
            self._validate_persistable(profile)
            default, profiles = self._load()
            if profile.profile_id in profiles:
                raise DuplicateProfileError(
                    f"Profile '{profile.profile_id}' already exists; "
                    "use update explicitly."
                )
            profiles[profile.profile_id] = profile.model_copy(deep=True)
            self._write(default, profiles)

    def update(self, profile: OpenAICompatibleProfile) -> None:
        """Explicitly replace an existing profile with the same identity."""
        with self._lock:
            self._validate_persistable(profile)
            default, profiles = self._load()
            if profile.profile_id not in profiles:
                raise ProfileNotFoundError(
                    f"Profile '{profile.profile_id}' was not found."
                )
            profiles[profile.profile_id] = profile.model_copy(deep=True)
            self._write(default, profiles)

    def remove(self, profile_id: str) -> None:
        """Delete a profile and clear the default if it referred to that profile."""
        with self._lock:
            default, profiles = self._load()
            if profile_id not in profiles:
                raise ProfileNotFoundError(f"Profile '{profile_id}' was not found.")
            del profiles[profile_id]
            self._write(None if default == profile_id else default, profiles)

    def default_profile_id(self) -> str | None:
        """Return the stored default profile identity, if any."""
        with self._lock:
            default, _ = self._load()
            return default

    def get_default(self) -> OpenAICompatibleProfile | None:
        """Return a detached default profile, or ``None`` when none is selected."""
        with self._lock:
            default, profiles = self._load()
            if default is None:
                return None
            return profiles[default].model_copy(deep=True)

    def set_default(self, profile_id: str) -> None:
        """Set an existing profile as default."""
        with self._lock:
            _, profiles = self._load()
            if profile_id not in profiles:
                raise ProfileNotFoundError(f"Profile '{profile_id}' was not found.")
            self._write(profile_id, profiles)

    def _load(self) -> tuple[str | None, dict[str, OpenAICompatibleProfile]]:
        if not self._path.exists():
            return None, {}
        self._reject_symlink()
        try:
            raw = self._path.read_text(encoding="utf-8")
            document: object = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProfileStoreError(
                "Profile store is malformed or cannot be read."
            ) from error
        if not isinstance(document, dict):
            raise ProfileStoreError("Profile store must contain a JSON object.")
        if document.get("version") != _FORMAT_VERSION:
            version = document.get("version")
            if isinstance(version, int) and version > _FORMAT_VERSION:
                raise ProfileStoreError(
                    f"Profile store format version {version} is newer than supported."
                )
            raise ProfileStoreError("Profile store has an unsupported format version.")
        raw_profiles = document.get("profiles")
        if not isinstance(raw_profiles, list):
            raise ProfileStoreError("Profile store profiles must be a list.")
        profiles: dict[str, OpenAICompatibleProfile] = {}
        for entry in raw_profiles:
            try:
                profile = OpenAICompatibleProfile.model_validate(entry)
            except ValidationError as error:
                raise ProfileStoreError(
                    "Profile store contains an invalid profile."
                ) from error
            if profile.profile_id in profiles:
                raise ProfileStoreError("Profile store contains duplicate profile IDs.")
            self._validate_persistable(profile)
            profiles[profile.profile_id] = profile
        default = document.get("default_profile_id")
        if default is not None and (
            not isinstance(default, str) or default not in profiles
        ):
            raise ProfileStoreError("Profile store default profile is invalid.")
        return default, profiles

    def _write(
        self, default: str | None, profiles: dict[str, OpenAICompatibleProfile]
    ) -> None:
        self._reject_symlink()
        parent = self._path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "default_profile_id": default,
                "profiles": [
                    profiles[key].model_dump(mode="json") for key in sorted(profiles)
                ],
                "version": _FORMAT_VERSION,
            }
            serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self._path.name}.", dir=parent, text=True
            )
            temporary = Path(temporary_name)
            try:
                os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    descriptor = -1
                    handle.write(serialized)
                    handle.flush()
                    os.fsync(handle.fileno())
                # Recheck immediately before replacement so a symlink is never followed.
                self._reject_symlink()
                os.replace(temporary, self._path)
                with contextlib.suppress(OSError):
                    os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
            except BaseException:
                if descriptor != -1:
                    os.close(descriptor)
                with contextlib.suppress(OSError):
                    temporary.unlink(missing_ok=True)
                raise
        except ProfileStoreError:
            raise
        except OSError as error:
            raise ProfileStoreError(
                "Profile store could not be written safely."
            ) from error

    def _reject_symlink(self) -> None:
        try:
            if self._path.is_symlink():
                raise ProfileStoreError("Profile store path must not be a symlink.")
        except OSError as error:
            raise ProfileStoreError(
                "Profile store path cannot be inspected safely."
            ) from error

    @staticmethod
    def _validate_persistable(profile: OpenAICompatibleProfile) -> None:
        """Accept only references the built-in resolver can safely interpret."""
        if (
            profile.api_key_secret_ref is not None
            and not profile.api_key_secret_ref.startswith("env:")
        ):
            raise ProfileStoreError(
                "Persistent API-key configuration must be an env: secret reference."
            )
