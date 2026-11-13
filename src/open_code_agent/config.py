"""Application configuration."""

import os
import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_data_dir() -> Path:
    """Return the platform-appropriate data location without creating it."""
    if sys.platform == "win32":
        return Path(
            os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        ) / ("OpenCodeAgent")
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OpenCodeAgent"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / (
        "open-code-agent"
    )


class Settings(BaseSettings):
    """Settings loaded from ``OCA_``-prefixed environment variables."""

    model_config = SettingsConfigDict(env_prefix="OCA_", case_sensitive=False)

    log_level: str = "INFO"
    data_dir: Path = Field(default_factory=default_data_dir)
    request_timeout_seconds: int = 60
    max_agent_steps: int = 30
