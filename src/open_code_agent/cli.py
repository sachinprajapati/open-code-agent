"""Command-line interface for OpenCodeAgent."""

import asyncio
import platform
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from open_code_agent import __version__
from open_code_agent.execution import (
    ApprovalDecision,
    CommandApprovalRequest,
    CommandClassifier,
    CommandExecutionPolicy,
    CommandProposal,
    ProcessRunner,
)
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


@app.command("command-run")
def command_run(
    workspace: Annotated[Path, typer.Option("--workspace")],
    executable: Annotated[str, typer.Option("--executable")],
    reason: Annotated[str, typer.Option("--reason")],
    arguments: Annotated[list[str] | None, typer.Option("--argument")] = None,
    working_directory: Annotated[str, typer.Option("--working-directory")] = ".",
    timeout_seconds: Annotated[float, typer.Option("--timeout-seconds")] = 120.0,
) -> None:
    """Run one explicitly approved, structured development command.

    This command intentionally enables only the conservative default policy;
    shell, network, package, Git mutation, and formatter-write commands remain
    blocked even when a user tries to approve them.
    """
    context = _workspace_from_cli_input(workspace)
    command_arguments = arguments or []
    try:
        proposal = CommandProposal.create(
            executable=executable,
            arguments=command_arguments,
            working_directory=working_directory,
            timeout_seconds=timeout_seconds,
            reason=reason,
        )
        policy = CommandExecutionPolicy(execution_enabled=True)
        classification = CommandClassifier().classify(proposal)
        policy.validate(proposal, classification)
    except Exception as error:
        raise typer.BadParameter(str(error)) from error
    request = CommandApprovalRequest(proposal, classification, proposal.fingerprint)
    decision = _cli_approval(request)
    if decision is not ApprovalDecision.APPROVED_ONCE:
        console.print("Command was not executed.")
        raise typer.Exit(code=1)
    if request.fingerprint != proposal.fingerprint:
        console.print("Command proposal changed after approval.")
        raise typer.Exit(code=1)
    try:
        result = asyncio.run(
            ProcessRunner(context, policy).run(proposal, classification)
        )
    except KeyboardInterrupt:
        console.print("Command run cancelled.")
        raise typer.Exit(code=1) from None
    console.print(Panel.fit(result.stdout or "(no stdout)", title="stdout"))
    if result.stderr:
        console.print(Panel.fit(result.stderr, title="stderr"))
    console.print(f"Exit code: {result.exit_code}; timed out: {result.timed_out}")
    if result.timed_out or result.exit_code not in {0, None}:
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


def _cli_approval(request: CommandApprovalRequest) -> ApprovalDecision:
    """Render a human review panel; non-interactive invocation denies by default."""
    proposal = request.proposal
    classification = request.classification
    console.print(
        Panel.fit(
            "\n".join(
                [
                    f"Command: {proposal.display_argv}",
                    f"Working directory: {proposal.working_directory.as_posix()}",
                    f"Reason: {proposal.reason}",
                    f"Classification: {classification.category.value}",
                    f"Risk: {classification.risk.value}",
                    f"Timeout: {proposal.timeout_seconds:g}s",
                    f"May modify files: {classification.may_modify_files}",
                    f"May use network: {classification.may_use_network}",
                    f"Fingerprint: {request.fingerprint}",
                ]
            ),
            title="Approve command once?",
        )
    )
    if not sys.stdin.isatty():
        return ApprovalDecision.DENIED
    try:
        choice = typer.prompt("[a]pprove once, [d]eny (default), [c]ancel", default="d")
    except (KeyboardInterrupt, EOFError):
        return ApprovalDecision.CANCELLED
    return {
        "a": ApprovalDecision.APPROVED_ONCE,
        "c": ApprovalDecision.CANCELLED,
    }.get(choice.strip().lower(), ApprovalDecision.DENIED)
