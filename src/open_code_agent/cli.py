"""Command-line interface for OpenCodeAgent."""

import platform
import shutil
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from open_code_agent import __version__
from open_code_agent.workspace import (
    PatchProposal,
    PatchProposalStore,
    WorkspaceContext,
    WorkspaceError,
)

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


@app.command("patch-preview")
def patch_preview(
    workspace: Annotated[Path, typer.Option("--workspace")],
    diff_file: Annotated[Path, typer.Option("--diff-file")],
) -> None:
    """Validate and display a patch proposal without modifying the workspace."""
    proposal = _proposal_from_cli_inputs(workspace, diff_file)
    _display_proposal(proposal)


@app.command("patch-apply")
def patch_apply(
    workspace: Annotated[Path, typer.Option("--workspace")],
    diff_file: Annotated[Path, typer.Option("--diff-file")],
) -> None:
    """Preview then apply a proposal only after an interactive user approval."""
    store = PatchProposalStore(_workspace_from_cli_input(workspace))
    proposal = _proposal_from_diff_file(store, diff_file)
    _display_proposal(proposal)
    if not typer.confirm("Apply this proposal?"):
        console.print("Patch proposal was not applied.")
        raise typer.Exit(code=1)
    store.approve(proposal.proposal_id)
    result = store.apply_approved(proposal.proposal_id)
    if result.complete:
        console.print(f"Applied proposal {result.proposal_id}.")
        return
    console.print("Patch application was only partially completed.")
    raise typer.Exit(code=1)


def _proposal_from_cli_inputs(workspace: Path, diff_file: Path) -> PatchProposal:
    """Construct a short-lived proposal store for CLI preview-only requests."""
    store = PatchProposalStore(_workspace_from_cli_input(workspace))
    return _proposal_from_diff_file(store, diff_file)


def _workspace_from_cli_input(workspace: Path) -> WorkspaceContext:
    """Convert expected workspace setup failures to concise CLI validation errors."""
    try:
        return WorkspaceContext(root=workspace)
    except WorkspaceError as error:
        raise typer.BadParameter(str(error), param_hint="--workspace") from error


def _proposal_from_diff_file(
    store: PatchProposalStore, diff_file: Path
) -> PatchProposal:
    """Read a bounded user-selected diff file and validate it as one proposal."""
    try:
        limit = store.max_patch_bytes
        if diff_file.stat().st_size > limit:
            raise typer.BadParameter("Diff file exceeds the configured patch limit.")
        diff = diff_file.read_text(encoding="utf-8")
        return store.propose(diff)
    except OSError as error:
        raise typer.BadParameter(
            "Diff file cannot be read.", param_hint="--diff-file"
        ) from error
    except WorkspaceError as error:
        raise typer.BadParameter(str(error), param_hint="--diff-file") from error


def _display_proposal(proposal: PatchProposal) -> None:
    """Render validated proposal identity, targets, and its original diff."""
    console.print(f"Proposal: {proposal.proposal_id}")
    console.print("Affected paths:")
    for path in proposal.affected_paths:
        console.print(f"- {path}")
    console.print(proposal.diff, markup=False)
