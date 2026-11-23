"""One composition point for bounded CLI coding-agent sessions."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from open_code_agent.agent.limits import AgentLimits
from open_code_agent.agent.runner import AgentRunner
from open_code_agent.execution import ApprovalHandler, CommandExecutionPolicy
from open_code_agent.mcp import ManagedMCPServer, register_mcp_server
from open_code_agent.providers import (
    EnvironmentSecretResolver,
    OpenAICompatibleProfile,
    OpenAICompatibleProvider,
    OpenAIResponsesProvider,
    SecretResolver,
    provider_for_profile,
)
from open_code_agent.tools import (
    ListFilesTool,
    PatchApprovalHandler,
    ProposePatchTool,
    ReadFileTool,
    RunCommandTool,
    SearchCodeTool,
    ToolRegistry,
)
from open_code_agent.workspace import (
    PatchCheckpointRecorder,
    PatchProposalStore,
    WorkspaceContext,
)


class AgentMode(StrEnum):
    """CLI-facing fixed capability sets for a single session."""

    PLAN = "plan"
    ASK = "ask"
    AUTO_EDIT = "auto-edit"


@dataclass
class AgentSession:
    """Own one provider, one tool registry, and one workspace-bound runner."""

    profile: OpenAICompatibleProfile
    model: str
    workspace: WorkspaceContext
    mode: AgentMode
    runner: AgentRunner
    provider: OpenAICompatibleProvider | OpenAIResponsesProvider
    tools: ToolRegistry
    limits: AgentLimits
    mcp_servers: tuple[ManagedMCPServer, ...] = ()

    async def aclose(self) -> None:
        """Release provider-owned HTTP resources."""
        for server in self.mcp_servers:
            await server.close()
        await self.provider.aclose()

    async def __aenter__(self) -> "AgentSession":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


def build_agent_session(
    *,
    profile: OpenAICompatibleProfile,
    workspace: WorkspaceContext,
    mode: AgentMode,
    model: str | None = None,
    secret_resolver: SecretResolver | None = None,
    command_policy: CommandExecutionPolicy | None = None,
    command_approvals: ApprovalHandler | None = None,
    patch_approvals: PatchApprovalHandler | None = None,
    patch_checkpoint_recorder: PatchCheckpointRecorder | None = None,
    limits: AgentLimits | None = None,
    mcp_servers: Sequence[ManagedMCPServer] = (),
) -> AgentSession:
    """Build an isolated session without mutating profile configuration.

    Plan mode constructs only read tools.  Command execution is intentionally
    not constructed unless both a policy and approval handler are supplied.
    """
    tools = ToolRegistry()
    tools.register(ListFilesTool(workspace))
    tools.register(ReadFileTool(workspace))
    tools.register(SearchCodeTool(workspace))
    if mode is not AgentMode.PLAN:
        tools.register(
            ProposePatchTool(
                PatchProposalStore(workspace, patch_checkpoint_recorder),
                patch_approvals,
            )
        )
        if command_policy is not None and command_approvals is not None:
            tools.register(RunCommandTool(workspace, command_policy, command_approvals))
    profile_copy = profile.model_copy(deep=True)
    provider = provider_for_profile(
        profile_copy, secret_resolver=secret_resolver or EnvironmentSecretResolver()
    )
    effective_limits = (limits or AgentLimits()).model_copy(deep=True)
    return AgentSession(
        profile=profile.model_copy(deep=True),
        model=model or profile.default_model,
        workspace=workspace,
        mode=mode,
        runner=AgentRunner(provider, tools),
        provider=provider,
        tools=tools,
        limits=effective_limits,
        mcp_servers=tuple(mcp_servers),
    )


async def attach_mcp_servers(session: AgentSession) -> None:
    """Attach already user-enabled MCP servers after session construction.

    This explicit asynchronous step ensures a configuration record alone can
    never launch code. Callers invoke it only after an editor-initiated
    connect/enable action and can surface any safe startup failure.
    """
    for server in session.mcp_servers:
        await register_mcp_server(server, session.tools)
