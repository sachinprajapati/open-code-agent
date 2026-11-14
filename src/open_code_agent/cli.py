"""Command-line interface for OpenCodeAgent."""

import asyncio
import json
import platform
import shutil
import sys
from enum import IntEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from open_code_agent import __version__
from open_code_agent.agent import (
    AgentLimits,
    AgentMode,
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
    AgentSession,
    build_agent_session,
)
from open_code_agent.agent.events import (
    ModelEventReceived,
    ModelStepStarted,
    RunCompleted,
    RunFailed,
    RunLimitReached,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from open_code_agent.agent.prompt import coding_agent_system_prompt
from open_code_agent.config import Settings
from open_code_agent.execution import (
    ApprovalDecision,
    CommandApprovalRequest,
    CommandClassifier,
    CommandExecutionPolicy,
    CommandProposal,
    ProcessRunner,
)
from open_code_agent.persistence import (
    ProfileNotFoundError,
    ProfileStoreError,
    ProviderProfileStore,
)
from open_code_agent.protocol import AgentMessage, TextDelta, ToolCallCompleted
from open_code_agent.providers import OpenAICompatibleProfile, OpenAICompatibleProvider
from open_code_agent.tools import PatchApprovalRequest
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
provider_app = typer.Typer(help="Manage persistent OpenAI-compatible profiles.")
app.add_typer(provider_app, name="provider")
console = Console()


class ExitCode(IntEnum):
    """Stable process statuses for scripting the CLI."""

    SUCCESS = 0
    CONFIGURATION = 2
    PROVIDER = 3
    LIMIT = 4
    CANCELLED = 5
    INTERNAL = 10


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


@app.command("app-server")
def app_server(
    profile_store: Annotated[Path | None, typer.Option("--profile-store")] = None,
    log_level: Annotated[str, typer.Option("--log-level")] = "INFO",
    debug: Annotated[bool, typer.Option("--debug")] = False,
    max_sessions: Annotated[int, typer.Option("--max-sessions", min=1)] = 16,
    approval_timeout: Annotated[
        float, typer.Option("--approval-timeout", min=0.1)
    ] = 120.0,
) -> None:
    """Run the editor-facing JSON-RPC server over stdin/stdout only."""
    del log_level, debug  # Diagnostics intentionally remain quiet unless added safely.
    from open_code_agent.app_server import AppServer, AppServerLimits
    from open_code_agent.app_server.transport import StdioLineTransport

    store_path = profile_store or (Settings().data_dir / "profiles.json")
    limits = AppServerLimits(
        max_sessions=max_sessions, approval_timeout_seconds=approval_timeout
    )
    server = AppServer(
        StdioLineTransport(limits),
        profile_store=ProviderProfileStore(store_path),
        limits=limits,
    )
    try:
        asyncio.run(server.serve())
    except KeyboardInterrupt:
        # stdout must remain exclusively reserved for JSON-RPC messages.
        raise typer.Exit(ExitCode.SUCCESS) from None


@app.command("app-server-manifest")
def app_server_manifest(
    output: Annotated[Path, typer.Option("--output")] = Path(
        "docs/app-server.protocol.json"
    ),
) -> None:
    """Regenerate the deterministic app-server protocol manifest."""
    from open_code_agent.app_server.manifest import write_manifest

    write_manifest(output)


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


def _profile_store() -> ProviderProfileStore:
    """Create a store only when a profile command needs one."""
    return ProviderProfileStore(Settings().data_dir / "profiles.json")


def _profile_from_options(
    profile_id: str,
    display_name: str,
    base_url: str,
    default_model: str,
    api_key_secret_ref: str | None,
    request_timeout: float,
    connect_timeout: float,
    allow_insecure_http: bool,
) -> OpenAICompatibleProfile:
    if api_key_secret_ref is not None and not api_key_secret_ref.startswith("env:"):
        raise typer.BadParameter(
            "API-key references must use env:VARIABLE_NAME; raw API keys are "
            "not accepted.",
            param_hint="--api-key-secret-ref",
        )
    try:
        return OpenAICompatibleProfile(
            profile_id=profile_id,
            display_name=display_name,
            base_url=base_url,
            default_model=default_model,
            api_key_secret_ref=api_key_secret_ref,
            request_timeout_seconds=request_timeout,
            connect_timeout_seconds=connect_timeout,
            allow_insecure_http=allow_insecure_http,
        )
    except Exception as error:
        raise typer.BadParameter(str(error)) from error


@provider_app.command("add")
def provider_add(
    profile_id: Annotated[str, typer.Option("--profile-id")],
    display_name: Annotated[str, typer.Option("--display-name")],
    base_url: Annotated[str, typer.Option("--base-url")],
    default_model: Annotated[str, typer.Option("--default-model")],
    api_key_secret_ref: Annotated[
        str | None,
        typer.Option(
            "--api-key-secret-ref",
            help=(
                "Secret reference only, for example env:OPENAI_API_KEY; "
                "never a raw key."
            ),
        ),
    ] = None,
    request_timeout: Annotated[float, typer.Option("--request-timeout")] = 60.0,
    connect_timeout: Annotated[float, typer.Option("--connect-timeout")] = 10.0,
    allow_insecure_http: Annotated[bool, typer.Option("--allow-insecure-http")] = False,
    replace: Annotated[
        bool, typer.Option("--replace", help="Explicitly replace an existing profile.")
    ] = False,
) -> None:
    """Add a non-secret profile, or explicitly replace an existing one."""
    profile = _profile_from_options(
        profile_id,
        display_name,
        base_url,
        default_model,
        api_key_secret_ref,
        request_timeout,
        connect_timeout,
        allow_insecure_http,
    )
    try:
        store = _profile_store()
        if replace:
            store.update(profile)
        else:
            store.add(profile)
    except ProfileStoreError as error:
        console.print(f"Profile error: {error}", style="red")
        raise typer.Exit(ExitCode.CONFIGURATION) from None
    console.print(f"Saved profile '{profile.profile_id}'.")


@provider_app.command("list")
def provider_list() -> None:
    """List profiles without resolving any secret reference."""
    try:
        store = _profile_store()
        default = store.default_profile_id()
        profiles = store.list()
    except ProfileStoreError as error:
        console.print(f"Profile error: {error}", style="red")
        raise typer.Exit(ExitCode.CONFIGURATION) from None
    if not profiles:
        console.print("No provider profiles configured.")
        return
    table = Table("Profile ID", "Name", "Default")
    for profile in profiles:
        table.add_row(
            profile.profile_id,
            profile.display_name,
            "yes" if profile.profile_id == default else "",
        )
    console.print(table)


@provider_app.command("show")
def provider_show(profile_id: str) -> None:
    """Show stored non-secret profile configuration."""
    try:
        profile = _profile_store().get(profile_id)
    except ProfileStoreError as error:
        console.print(f"Profile error: {error}", style="red")
        raise typer.Exit(ExitCode.CONFIGURATION) from None
    console.print_json(json.dumps(profile.model_dump(mode="json"), sort_keys=True))


@provider_app.command("remove")
def provider_remove(
    profile_id: str,
    yes: Annotated[
        bool, typer.Option("--yes", help="Confirm removal in non-interactive use.")
    ] = False,
) -> None:
    """Remove a profile after an explicit confirmation."""
    if not yes:
        if not sys.stdin.isatty():
            console.print("Non-interactive removal requires --yes.", style="red")
            raise typer.Exit(ExitCode.CONFIGURATION)
        if not typer.confirm(f"Remove profile '{profile_id}'?"):
            console.print("Profile was not removed.")
            raise typer.Exit(ExitCode.CANCELLED)
    try:
        _profile_store().remove(profile_id)
    except ProfileStoreError as error:
        console.print(f"Profile error: {error}", style="red")
        raise typer.Exit(ExitCode.CONFIGURATION) from None
    console.print(f"Removed profile '{profile_id}'.")


@provider_app.command("set-default")
def provider_set_default(profile_id: str) -> None:
    """Select the stored default profile."""
    try:
        _profile_store().set_default(profile_id)
    except ProfileStoreError as error:
        console.print(f"Profile error: {error}", style="red")
        raise typer.Exit(ExitCode.CONFIGURATION) from None
    console.print(f"Default profile: {profile_id}")


def _provider_for_profile(profile: OpenAICompatibleProfile) -> OpenAICompatibleProvider:
    """Small seam for network-free CLI tests; caller owns the returned provider."""
    return OpenAICompatibleProvider(profile)


async def _provider_test(profile: OpenAICompatibleProfile) -> tuple[bool, str]:
    provider = _provider_for_profile(profile)
    try:
        result = await provider.validate_configuration()
        if result.valid:
            return True, "Connection succeeded."
        detail = result.diagnostic or (
            result.error.message if result.error else "failed"
        )
        return False, f"Connection failed: {detail}"
    finally:
        await provider.aclose()


@provider_app.command("test")
def provider_test(profile_id: str) -> None:
    """Validate a profile while resolving its secret only for this request."""
    try:
        profile = _profile_store().get(profile_id)
        valid, message = asyncio.run(_provider_test(profile))
    except KeyboardInterrupt:
        console.print("Provider test cancelled.")
        raise typer.Exit(ExitCode.CANCELLED) from None
    except ProfileStoreError as error:
        console.print(f"Profile error: {error}", style="red")
        raise typer.Exit(ExitCode.CONFIGURATION) from None
    except Exception:
        console.print("Provider test failed.", style="red")
        raise typer.Exit(ExitCode.PROVIDER) from None
    console.print(message, style="green" if valid else "red")
    if not valid:
        raise typer.Exit(ExitCode.PROVIDER)


async def _provider_models(profile: OpenAICompatibleProfile) -> tuple[str, ...]:
    provider = _provider_for_profile(profile)
    try:
        return tuple(model.id for model in await provider.list_models())
    finally:
        await provider.aclose()


@provider_app.command("models")
def provider_models(profile_id: str) -> None:
    """List model IDs in deterministic order without exposing credentials."""
    try:
        profile = _profile_store().get(profile_id)
        models = asyncio.run(_provider_models(profile))
    except KeyboardInterrupt:
        console.print("Model listing cancelled.")
        raise typer.Exit(ExitCode.CANCELLED) from None
    except ProfileStoreError as error:
        console.print(f"Profile error: {error}", style="red")
        raise typer.Exit(ExitCode.CONFIGURATION) from None
    except Exception:
        console.print("Model listing failed.", style="red")
        raise typer.Exit(ExitCode.PROVIDER) from None
    if not models:
        console.print("No models reported by provider.")
        return
    for model_id in sorted(models):
        console.print(model_id)


class _InteractiveCommandApprovals:
    """Adapt the established CLI command review UI to the async tool boundary."""

    def __init__(self) -> None:
        self.cancelled = False

    async def request_approval(
        self, request: CommandApprovalRequest
    ) -> ApprovalDecision:
        decision = _cli_approval(request)
        self.cancelled = decision is ApprovalDecision.CANCELLED
        return decision


class _InteractivePatchApprovals:
    """One-time patch review for model-generated workspace edits."""

    def __init__(self) -> None:
        self.cancelled = False

    async def request_patch_approval(self, request: PatchApprovalRequest) -> bool:
        proposal = request.proposal
        console.print(f"Patch affects: {', '.join(proposal.affected_paths)}")
        console.print(proposal.diff, markup=False)
        if not sys.stdin.isatty():
            return False
        try:
            return typer.confirm("Apply this patch?")
        except (KeyboardInterrupt, EOFError):
            self.cancelled = True
            return False


def _selected_profile(profile_id: str | None) -> OpenAICompatibleProfile:
    store = _profile_store()
    selected = profile_id or store.default_profile_id()
    if selected is None:
        raise ProfileNotFoundError(
            "No profile selected and no default provider profile is configured."
        )
    return store.get(selected)


def _agent_workspace(path: Path | None) -> WorkspaceContext:
    """The CLI alone may make an omitted workspace mean its current directory."""
    return _workspace_from_cli_input((path or Path.cwd()).resolve())


def _safe_argument_summary(arguments: object) -> str:
    """Describe argument shape without echoing user/model-provided values."""
    if not isinstance(arguments, dict):
        return "arguments unavailable"
    keys = sorted(str(key) for key in arguments)[:8]
    suffix = ", …" if len(arguments) > len(keys) else ""
    return f"arguments: {', '.join(keys)}{suffix}" if keys else "no arguments"


def _safe_result_summary(content: object) -> str:
    """Describe bounded result shape without printing workspace or secret content."""
    if not isinstance(content, dict):
        return "result received"
    keys = sorted(str(key) for key in content)[:8]
    suffix = ", …" if len(content) > len(keys) else ""
    return f"result fields: {', '.join(keys)}{suffix}" if keys else "empty result"


async def _run_agent_turn(
    session: AgentSession,
    task: str,
    history: list[AgentMessage],
    verbose: bool,
) -> tuple[AgentRunResult, bool]:
    """Stream one turn and report whether its terminal history is committable."""
    # Kept here rather than in commands so one-shot and chat have identical rendering.
    request = AgentRunRequest(
        model=session.model,
        initial_messages=history,
        task=task,
        system_prompt=None if history else coding_agent_system_prompt(session.mode),
        limits=session.limits,
    )
    terminal: RunCompleted | RunFailed | RunLimitReached | None = None
    printed_text = False
    async for event in session.runner.stream(request):
        if isinstance(event, ModelEventReceived) and isinstance(
            event.model_event, TextDelta
        ):
            console.print(event.model_event.text, end="", markup=False)
            printed_text = True
        elif isinstance(event, ModelEventReceived) and isinstance(
            event.model_event, ToolCallCompleted
        ):
            call = event.model_event.tool_call
            console.print(
                f"\nTool request {call.name}: {_safe_argument_summary(call.arguments)}",
                style="dim",
            )
        elif verbose and isinstance(event, ModelStepStarted):
            console.print(f"\n[step {event.step}]", style="dim")
        elif isinstance(event, ToolExecutionStarted):
            console.print(f"\nUsing tool: {event.tool_name}", style="dim")
        elif isinstance(event, ToolExecutionCompleted):
            state = "failed" if event.result.is_error else "completed"
            console.print(
                f"Tool {event.tool_name} {state}; "
                f"{_safe_result_summary(event.result.content)}.",
                style="dim",
            )
        if isinstance(event, (RunCompleted, RunFailed, RunLimitReached)):
            terminal = event
    if printed_text:
        console.print()
    if terminal is None:
        raise RuntimeError("Agent stream ended without a terminal event.")
    if isinstance(terminal, RunCompleted):
        usage = terminal.result.usage
        console.print(
            f"Usage: input {usage.input_tokens}, output {usage.output_tokens}, "
            f"total {usage.total_tokens}.",
            style="dim",
        )
        return terminal.result, True
    if isinstance(terminal, RunLimitReached):
        console.print(
            f"Agent limit reached: {terminal.result.limit_reason}", style="red"
        )
    else:
        console.print("Agent run failed.", style="red")
    return terminal.result, False


async def _ask_async(
    task: str,
    profile_id: str | None,
    model: str | None,
    workspace_path: Path | None,
    mode: AgentMode,
    max_steps: int,
    timeout: float | None,
    verbose: bool,
) -> ExitCode:
    profile = _selected_profile(profile_id)
    workspace = _agent_workspace(workspace_path)
    limits = AgentLimits(max_model_steps=max_steps, overall_timeout_seconds=timeout)
    command_approvals = _InteractiveCommandApprovals()
    patch_approvals = _InteractivePatchApprovals()
    async with build_agent_session(
        profile=profile,
        workspace=workspace,
        mode=mode,
        model=model,
        limits=limits,
        command_policy=CommandExecutionPolicy(execution_enabled=True),
        command_approvals=command_approvals,
        patch_approvals=patch_approvals,
    ) as session:
        console.print(
            f"Profile: {profile.profile_id} | Model: {session.model} | "
            f"Workspace: {workspace.root.name} | Mode: {mode.value}"
        )
        if mode is AgentMode.AUTO_EDIT:
            console.print(
                "Auto-edit currently uses the same patch approval as ask mode.",
                style="dim",
            )
        result, complete = await _run_agent_turn(session, task, [], verbose)
        if command_approvals.cancelled or patch_approvals.cancelled:
            return ExitCode.CANCELLED
        if complete:
            return ExitCode.SUCCESS
        return (
            ExitCode.LIMIT
            if getattr(result, "status", None) is AgentRunStatus.LIMIT_REACHED
            else ExitCode.PROVIDER
        )


@app.command("ask")
def ask(
    task: str,
    profile_id: Annotated[str | None, typer.Option("--profile")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    workspace: Annotated[Path | None, typer.Option("--workspace")] = None,
    mode: Annotated[AgentMode, typer.Option("--mode")] = AgentMode.ASK,
    max_steps: Annotated[int, typer.Option("--max-steps", min=1)] = 30,
    timeout: Annotated[float | None, typer.Option("--timeout", min=0.1)] = None,
    no_color: Annotated[bool, typer.Option("--no-color")] = False,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> None:
    """Run one streaming, bounded coding-agent task."""
    if no_color:
        console.no_color = True
    try:
        exit_code = asyncio.run(
            _ask_async(
                task,
                profile_id,
                model,
                workspace,
                mode,
                max_steps,
                timeout,
                verbose,
            )
        )
    except KeyboardInterrupt:
        console.print("Agent run cancelled.")
        raise typer.Exit(ExitCode.CANCELLED) from None
    except (ProfileStoreError, WorkspaceError) as error:
        console.print(f"Configuration error: {error}", style="red")
        raise typer.Exit(ExitCode.CONFIGURATION) from None
    except Exception:
        console.print("Agent could not be started safely.", style="red")
        raise typer.Exit(ExitCode.INTERNAL) from None
    if exit_code is not ExitCode.SUCCESS:
        raise typer.Exit(exit_code)


@app.command("chat")
def chat(
    profile_id: Annotated[str | None, typer.Option("--profile")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    workspace: Annotated[Path | None, typer.Option("--workspace")] = None,
    mode: Annotated[AgentMode, typer.Option("--mode")] = AgentMode.ASK,
    max_steps: Annotated[int, typer.Option("--max-steps", min=1)] = 30,
    timeout: Annotated[float | None, typer.Option("--timeout", min=0.1)] = None,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> None:
    """Start an in-memory multi-turn agent conversation."""
    try:
        asyncio.run(
            _chat_async(profile_id, model, workspace, mode, max_steps, timeout, verbose)
        )
    except KeyboardInterrupt:
        console.print("Chat cancelled.")
        raise typer.Exit(ExitCode.CANCELLED) from None
    except (ProfileStoreError, WorkspaceError) as error:
        console.print(f"Configuration error: {error}", style="red")
        raise typer.Exit(ExitCode.CONFIGURATION) from None


async def _chat_async(
    profile_id: str | None,
    model: str | None,
    workspace_path: Path | None,
    mode: AgentMode,
    max_steps: int,
    timeout: float | None,
    verbose: bool,
) -> None:
    profile = _selected_profile(profile_id)
    workspace = _agent_workspace(workspace_path)
    command_approvals = _InteractiveCommandApprovals()
    patch_approvals = _InteractivePatchApprovals()
    async with build_agent_session(
        profile=profile,
        workspace=workspace,
        mode=mode,
        model=model,
        limits=AgentLimits(max_model_steps=max_steps, overall_timeout_seconds=timeout),
        command_policy=CommandExecutionPolicy(execution_enabled=True),
        command_approvals=command_approvals,
        patch_approvals=patch_approvals,
    ) as session:
        history: list[AgentMessage] = []
        if mode is AgentMode.AUTO_EDIT:
            console.print(
                "Auto-edit currently uses the same patch approval as ask mode.",
                style="dim",
            )
        console.print("Type /help for commands.")
        while True:
            try:
                text = typer.prompt("you")
            except EOFError:
                console.print()
                return
            if not text.strip():
                continue
            if text.startswith("/"):
                command = text.strip().lower()
                if command in {"/exit", "/quit"}:
                    return
                if command == "/help":
                    console.print("/help, /status, /clear, /exit, /quit")
                elif command == "/status":
                    turns = sum(
                        1
                        for message in history
                        if getattr(getattr(message, "role", None), "value", None)
                        == "user"
                    )
                    console.print(
                        f"Profile: {profile.profile_id}; Model: {session.model}; "
                        f"Workspace: {workspace.root}; Mode: {mode.value}; "
                        f"Turns: {turns}"
                    )
                elif command == "/clear":
                    if sys.stdin.isatty() and typer.confirm("Clear conversation?"):
                        history = []
                else:
                    console.print("Unknown command. Type /help.")
                continue
            result, complete = await _run_agent_turn(session, text, history, verbose)
            if command_approvals.cancelled or patch_approvals.cancelled:
                return
            if complete:
                history = [message.model_copy(deep=True) for message in result.messages]
