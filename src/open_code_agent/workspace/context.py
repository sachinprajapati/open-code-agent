"""Explicit, immutable-in-practice binding for one workspace capability."""

from copy import deepcopy
from pathlib import Path

from open_code_agent.protocol import JSONObject
from open_code_agent.workspace.exceptions import InvalidWorkspaceError
from open_code_agent.workspace.policy import WorkspacePolicy


class WorkspaceContext:
    """Bind tools to one canonical existing directory, never process CWD.

    Relative roots are intentionally rejected: initialization must receive a
    clear absolute authority.  After construction all operations use ``root``.
    """

    def __init__(
        self,
        *,
        root: str | Path,
        policy: WorkspacePolicy | None = None,
        name: str | None = None,
        metadata: JSONObject | None = None,
    ) -> None:
        candidate = Path(root)
        if not candidate.is_absolute():
            raise InvalidWorkspaceError("Workspace root must be an absolute path.")
        try:
            canonical = candidate.resolve(strict=True)
        except OSError as error:
            raise InvalidWorkspaceError("Workspace root does not exist.") from error
        if not canonical.is_dir():
            raise InvalidWorkspaceError("Workspace root must be a directory.")
        if name is not None and not name.strip():
            raise InvalidWorkspaceError("Workspace name must not be blank.")
        self._root = canonical
        self._policy = (policy or WorkspacePolicy()).model_copy(deep=True)
        self._name = name
        self._metadata = deepcopy(metadata) if metadata is not None else None

    @property
    def root(self) -> Path:
        """Return the canonical absolute workspace root."""
        return self._root

    @property
    def policy(self) -> WorkspacePolicy:
        """Return a detached policy snapshot rather than mutable internals."""
        return self._policy.model_copy(deep=True)

    @property
    def name(self) -> str | None:
        """Return the optional user-facing workspace name."""
        return self._name

    @property
    def metadata(self) -> JSONObject | None:
        """Return detached JSON-compatible metadata."""
        return deepcopy(self._metadata)
