"""Provider-profile CLI behavior without resolving secrets or using a network."""

from pathlib import Path

from typer.testing import CliRunner

from open_code_agent.cli import ExitCode, app

runner = CliRunner()


def test_add_list_show_and_noninteractive_remove(tmp_path: Path) -> None:
    environment = {"OCA_DATA_DIR": str(tmp_path / "data")}
    arguments = [
        "provider",
        "add",
        "--profile-id",
        "local",
        "--display-name",
        "Local test",
        "--base-url",
        "http://localhost:1234/v1",
        "--default-model",
        "model-a",
        "--api-key-secret-ref",
        "env:SAFE_TEST_KEY",
    ]
    assert runner.invoke(app, arguments, env=environment).exit_code == 0
    listed = runner.invoke(app, ["provider", "list"], env=environment)
    assert listed.exit_code == 0 and "local" in listed.stdout
    shown = runner.invoke(app, ["provider", "show", "local"], env=environment)
    assert shown.exit_code == 0 and "env:SAFE_TEST_KEY" in shown.stdout
    removed = runner.invoke(app, ["provider", "remove", "local"], env=environment)
    assert removed.exit_code == ExitCode.CONFIGURATION
    assert (
        runner.invoke(
            app, ["provider", "remove", "local", "--yes"], env=environment
        ).exit_code
        == 0
    )


def test_raw_key_reference_is_rejected(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "provider",
            "add",
            "--profile-id",
            "local",
            "--display-name",
            "Local",
            "--base-url",
            "http://localhost:1234/v1",
            "--default-model",
            "model",
            "--api-key-secret-ref",
            "a-raw-secret",
        ],
        env={"OCA_DATA_DIR": str(tmp_path)},
    )
    assert result.exit_code != 0
    assert "a-raw-secret" not in result.stdout
