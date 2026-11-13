"""Model-facing patch proposal tool that never writes workspace files."""

from open_code_agent.protocol import JSONObject
from open_code_agent.tools.base import (
    ToolExecutionContext,
    ToolExecutionResult,
    ToolRisk,
)
from open_code_agent.workspace import PatchProposalStore, WorkspaceError


class ProposePatchTool:
    """Validate and store a diff proposal for later CLI-only user approval."""

    name = "propose_patch"
    description = "Propose a bounded unified diff for explicit user approval."
    input_schema: JSONObject = {
        "type": "object",
        "required": ["diff"],
        "properties": {"diff": {"type": "string"}},
        "additionalProperties": False,
    }
    risk = ToolRisk.WRITE

    def __init__(self, proposals: PatchProposalStore) -> None:
        self._proposals = proposals

    async def execute(
        self, arguments: JSONObject, context: ToolExecutionContext
    ) -> ToolExecutionResult:
        """Store a validated proposal only; approval and writes stay in the CLI."""
        del context
        diff = arguments.get("diff")
        if not isinstance(diff, str):
            return ToolExecutionResult(
                content={"error": "Patch diff must be a string."}, is_error=True
            )
        try:
            proposal = self._proposals.propose(diff)
        except WorkspaceError as error:
            return ToolExecutionResult(
                content={
                    "error": {"code": type(error).__name__, "message": str(error)}
                },
                is_error=True,
            )
        return ToolExecutionResult(
            content={
                "proposal_id": proposal.proposal_id,
                "affected_paths": list(proposal.affected_paths),
                "status": proposal.status.value,
            }
        )
