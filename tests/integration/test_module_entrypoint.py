"""Module-entry-point integration test."""

import subprocess
import sys

from open_code_agent import __version__


def test_module_entrypoint() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "open_code_agent", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert __version__ in result.stdout
