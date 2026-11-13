"""Command-line interface for OpenCodeAgent."""

import platform
import shutil
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from open_code_agent import __version__

app = typer.Typer(
    help="OpenCodeAgent command-line interface.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the application version and exit.",
        ),
    ] = False,
) -> None:
    """OpenCodeAgent is a provider-agnostic coding-agent foundation."""


@app.command()
def doctor() -> None:
    """Inspect the local environment without changing it."""
    table = Table(title="OpenCodeAgent diagnostics")
    table.add_column("Check", style="bold")
    table.add_column("Value")
    table.add_row("Application version", __version__)
    table.add_row("Python version", platform.python_version())
    table.add_row("Operating system", platform.platform())
    table.add_row("Current working directory", str(Path.cwd()))
    table.add_row("Git available", "yes" if shutil.which("git") else "no")
    table.add_row("ripgrep (rg) available", "yes" if shutil.which("rg") else "no")
    console.print(table)
