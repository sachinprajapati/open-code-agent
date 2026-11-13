"""CLI tests."""

from typer.testing import CliRunner

from open_code_agent import __version__
from open_code_agent.cli import app

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_doctor_command() -> None:
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    for label in (
        "Application version",
        "Python version",
        "Operating system",
        "Current working directory",
        "Git available",
        "ripgrep (rg) available",
    ):
        assert label in result.stdout
