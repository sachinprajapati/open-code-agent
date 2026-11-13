"""Configuration tests."""

from pathlib import Path

from pytest import MonkeyPatch

from open_code_agent.config import Settings, default_data_dir


def test_settings_defaults() -> None:
    settings = Settings()

    assert settings.log_level == "INFO"
    assert settings.data_dir == default_data_dir()
    assert settings.request_timeout_seconds == 60
    assert settings.max_agent_steps == 30


def test_settings_environment_overrides(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("OCA_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("OCA_DATA_DIR", "/tmp/oca-data")
    monkeypatch.setenv("OCA_REQUEST_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("OCA_MAX_AGENT_STEPS", "5")

    settings = Settings()

    assert settings.log_level == "DEBUG"
    assert settings.data_dir == Path("/tmp/oca-data")
    assert settings.request_timeout_seconds == 15
    assert settings.max_agent_steps == 5
