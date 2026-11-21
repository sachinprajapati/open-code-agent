"""Capability-accurate system prompts for the bounded coding agent."""

from open_code_agent.agent.session import AgentMode


def coding_agent_system_prompt(mode: AgentMode, *, has_mcp_tools: bool = False) -> str:
    """Return a concise prompt that advertises only the supplied tools."""
    capabilities = {
        AgentMode.PLAN: (
            "You can inspect files with list_files, search_code, and read_file; "
            "you cannot edit files or run commands."
        ),
        AgentMode.ASK: (
            "You can inspect files, propose focused patches that require user "
            "approval, and propose bounded commands that require user approval."
        ),
        AgentMode.AUTO_EDIT: (
            "You can inspect files and propose focused patches for approval. "
            "Commands always require user approval."
        ),
    }[mode]
    mcp_capability = (
        " You may also use the explicitly listed mcp.* tools. Each MCP tool call "
        "requires one-time user approval."
        if has_mcp_tools
        else ""
    )
    return (
        "You are a coding agent operating inside one bounded workspace. "
        "Use list/search/read before making assumptions and inspect current "
        "content before edits. "
        f"{capabilities}{mcp_capability} "
        "Never claim a tool succeeded before seeing its result; respect denied "
        "approval. "
        "Do not request secrets or attempt access beyond the provided tools. "
        "When commands are available, run focused verification. Report remaining "
        "failures honestly and finish with a concise summary."
    )
