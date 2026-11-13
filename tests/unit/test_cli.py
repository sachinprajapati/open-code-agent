"""CLI tests."""

from pathlib import Path

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


def test_patch_apply_previews_approves_and_applies(tmp_path: Path) -> None:
    target = tmp_path / "message.txt"
    target.write_text("old\n")
    patch = tmp_path / "proposal.diff"
    patch.write_text("--- a/message.txt\n+++ b/message.txt\n@@ -1 +1 @@\n-old\n+new\n")

    result = runner.invoke(
        app,
        ["patch-apply", "--workspace", str(tmp_path), "--diff-file", str(patch)],
        input="y\n",
    )

    assert result.exit_code == 0
    assert "Affected paths" in result.stdout
    assert "Applied proposal" in result.stdout
    assert target.read_text() == "new\n"
