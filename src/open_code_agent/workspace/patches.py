"""Validated unified-diff proposals with explicit approval before writes."""

import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from uuid import uuid4

from open_code_agent.workspace.context import WorkspaceContext
from open_code_agent.workspace.exceptions import (
    BinaryFileError,
    FileTooLargeError,
    InvalidPatchError,
    InvalidTextEncodingError,
    ProposalApprovalError,
    ProposalNotFoundError,
    StaleProposalError,
    WorkspaceAccessDeniedError,
    WorkspaceError,
    WorkspacePathTypeError,
)
from open_code_agent.workspace.files import policy_allows_path, require_non_sensitive
from open_code_agent.workspace.paths import (
    relative_workspace_path,
    resolve_workspace_path,
)


class ProposalStatus(StrEnum):
    """The small lifecycle of an in-memory patch proposal."""

    PENDING = "pending"
    APPROVED = "approved"
    APPLIED = "applied"


@dataclass(frozen=True)
class PatchFileChange:
    """One fully prepared existing-file replacement within a proposal."""

    path: PurePosixPath
    original_hash: str
    original_bytes: bytes
    new_bytes: bytes


@dataclass(frozen=True)
class PatchProposal:
    """A validated, previewable proposal that has not necessarily been approved."""

    proposal_id: str
    diff: str
    changes: tuple[PatchFileChange, ...]
    status: ProposalStatus

    @property
    def affected_paths(self) -> tuple[str, ...]:
        """Return sorted portable paths without exposing host paths."""
        return tuple(change.path.as_posix() for change in self.changes)


@dataclass(frozen=True)
class PatchApplyResult:
    """Accurate outcome of sequential per-file atomic replacements."""

    proposal_id: str
    applied_paths: tuple[str, ...]
    complete: bool
    error: str | None = None


@dataclass(frozen=True)
class _Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[str, ...]


class PatchProposalStore:
    """Keep validated proposals in memory for one explicit workspace capability.

    Validation prepares all target contents before a proposal is returned. The
    store does not write during proposal or approval; callers must explicitly
    invoke ``apply_approved`` after a human-controlled CLI approval.
    """

    def __init__(self, workspace: WorkspaceContext) -> None:
        self._workspace = workspace
        self._proposals: dict[str, PatchProposal] = {}

    @property
    def max_patch_bytes(self) -> int:
        """Return the bounded diff-input size accepted by this store."""
        return self._workspace.policy.max_patch_bytes

    def propose(self, diff: str) -> PatchProposal:
        """Validate an entire bounded unified diff without touching any file."""
        if not isinstance(diff, str) or not diff.strip():
            raise InvalidPatchError("Patch proposal must not be empty.")
        encoded = diff.encode("utf-8")
        policy = self._workspace.policy
        if len(encoded) > policy.max_patch_bytes:
            raise InvalidPatchError("Patch proposal exceeds the configured size limit.")
        parsed = _parse_unified_diff(
            diff, policy.max_patch_files, policy.max_patch_hunks
        )
        if (
            sum(len(hunk.lines) for _, hunks in parsed for hunk in hunks)
            > policy.max_patch_lines
        ):
            raise InvalidPatchError("Patch proposal exceeds the configured line limit.")
        changes: list[PatchFileChange] = []
        for relative, hunks in parsed:
            path = self._eligible_file(relative)
            original = _read_text_bytes(path, self._workspace)
            new_text = _apply_hunks(_decode_patch_text(original), hunks)
            new_bytes = _encode_like_original(new_text, original)
            if len(new_bytes) > policy.max_file_size_bytes:
                raise FileTooLargeError(
                    "Patched file would exceed the workspace limit."
                )
            changes.append(
                PatchFileChange(
                    path=relative,
                    original_hash=_hash(original),
                    original_bytes=original,
                    new_bytes=new_bytes,
                )
            )
        proposal = PatchProposal(
            proposal_id=str(uuid4()),
            diff=diff,
            changes=tuple(sorted(changes, key=lambda change: change.path.as_posix())),
            status=ProposalStatus.PENDING,
        )
        self._proposals[proposal.proposal_id] = proposal
        return proposal

    def get(self, proposal_id: str) -> PatchProposal:
        """Return an immutable proposal or a local not-found error."""
        try:
            return self._proposals[proposal_id]
        except KeyError as error:
            raise ProposalNotFoundError("Patch proposal was not found.") from error

    def approve(self, proposal_id: str) -> PatchProposal:
        """Mark a proposal approved; this method itself never writes files."""
        proposal = self.get(proposal_id)
        if proposal.status is not ProposalStatus.PENDING:
            raise ProposalApprovalError("Patch proposal is not awaiting approval.")
        approved = PatchProposal(
            proposal_id=proposal.proposal_id,
            diff=proposal.diff,
            changes=proposal.changes,
            status=ProposalStatus.APPROVED,
        )
        self._proposals[proposal_id] = approved
        return approved

    def apply_approved(self, proposal_id: str) -> PatchApplyResult:
        """Revalidate and atomically replace exactly one approved proposal.

        All hashes and policy checks are completed before the first replacement.
        A malicious concurrent local process can still race a later replacement;
        partial results are reported instead of hidden or rolled back.
        """
        proposal = self.get(proposal_id)
        if proposal.status is not ProposalStatus.APPROVED:
            raise ProposalApprovalError("Patch proposal requires explicit approval.")
        prepared: list[tuple[PatchFileChange, Path]] = []
        for change in proposal.changes:
            path = self._eligible_file(change.path)
            current = _read_text_bytes(path, self._workspace)
            if _hash(current) != change.original_hash:
                raise StaleProposalError(
                    "A proposed workspace file changed after preview."
                )
            prepared.append((change, path))
        applied: list[str] = []
        try:
            for change, path in prepared:
                _atomic_replace(path, change.new_bytes)
                applied.append(change.path.as_posix())
        except OSError:
            return PatchApplyResult(
                proposal_id=proposal_id,
                applied_paths=tuple(applied),
                complete=False,
                error="A filesystem error interrupted patch application.",
            )
        applied_proposal = PatchProposal(
            proposal_id=proposal.proposal_id,
            diff=proposal.diff,
            changes=proposal.changes,
            status=ProposalStatus.APPLIED,
        )
        self._proposals[proposal_id] = applied_proposal
        return PatchApplyResult(
            proposal_id=proposal_id,
            applied_paths=tuple(applied),
            complete=True,
        )

    def _eligible_file(self, relative: PurePosixPath) -> Path:
        path = resolve_workspace_path(self._workspace, relative.as_posix())
        if not path.is_file():
            raise WorkspacePathTypeError(
                "Patch target must be an existing regular file."
            )
        if not policy_allows_path(self._workspace, path):
            require_non_sensitive(self._workspace, path)
            raise WorkspaceAccessDeniedError(
                "Patch target is denied by workspace policy."
            )
        resolved_relative = relative_workspace_path(self._workspace, path)
        if resolved_relative != relative:
            raise WorkspaceAccessDeniedError(
                "Patch target path is not stable in the workspace."
            )
        require_non_sensitive(self._workspace, path)
        return path


def _parse_unified_diff(
    diff: str, max_files: int, max_hunks: int
) -> list[tuple[PurePosixPath, tuple[_Hunk, ...]]]:
    lines = diff.splitlines(keepends=True)
    result: list[tuple[PurePosixPath, tuple[_Hunk, ...]]] = []
    index = 0
    total_hunks = 0
    seen_paths: set[PurePosixPath] = set()
    while index < len(lines):
        if not lines[index].startswith("--- "):
            raise InvalidPatchError("Patch must use paired unified-diff file headers.")
        old_path = _header_path(lines[index], "--- ")
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise InvalidPatchError("Patch is missing a new-file header.")
        new_path = _header_path(lines[index], "+++ ")
        index += 1
        if old_path != new_path:
            raise InvalidPatchError(
                "Only modifications to existing files are supported."
            )
        if old_path in seen_paths:
            raise InvalidPatchError("Patch contains duplicate target paths.")
        seen_paths.add(old_path)
        hunks: list[_Hunk] = []
        while index < len(lines) and lines[index].startswith("@@ "):
            hunk, index = _parse_hunk(lines, index)
            hunks.append(hunk)
            total_hunks += 1
            if total_hunks > max_hunks:
                raise InvalidPatchError(
                    "Patch proposal exceeds the configured hunk limit."
                )
        if not hunks:
            raise InvalidPatchError("Each patch file requires at least one hunk.")
        result.append((old_path, tuple(hunks)))
        if len(result) > max_files:
            raise InvalidPatchError("Patch proposal exceeds the configured file limit.")
    return result


def _header_path(line: str, prefix: str) -> PurePosixPath:
    raw = line.removeprefix(prefix).rstrip("\r\n").split("\t", maxsplit=1)[0]
    if raw in {"", "/dev/null"}:
        raise InvalidPatchError("Patch must modify existing workspace files only.")
    if raw.startswith("a/") or raw.startswith("b/"):
        raw = raw[2:]
    try:
        # Import locally to keep the patch parser independent of host CWD.
        from open_code_agent.workspace.paths import normalize_workspace_path

        return normalize_workspace_path(raw)
    except WorkspaceError as error:
        raise InvalidPatchError("Patch contains an invalid workspace path.") from error


def _parse_hunk(lines: list[str], index: int) -> tuple[_Hunk, int]:
    header = lines[index].rstrip("\r\n")
    parts = header.split(" ")
    if len(parts) < 3 or not parts[1].startswith("-") or not parts[2].startswith("+"):
        raise InvalidPatchError("Patch hunk header is malformed.")
    old_start, old_count = _range(parts[1][1:])
    new_start, new_count = _range(parts[2][1:])
    index += 1
    body: list[str] = []
    old_seen = 0
    new_seen = 0
    while index < len(lines) and not lines[index].startswith(("@@ ", "--- ")):
        line = lines[index]
        if line.startswith("\\ No newline"):
            raise InvalidPatchError("Patches without final newlines are not supported.")
        if not line.startswith((" ", "+", "-")):
            raise InvalidPatchError("Patch hunk contains an invalid line.")
        body.append(line)
        if line[0] in {" ", "-"}:
            old_seen += 1
        if line[0] in {" ", "+"}:
            new_seen += 1
        index += 1
    if old_seen != old_count or new_seen != new_count:
        raise InvalidPatchError("Patch hunk line counts do not match its header.")
    return _Hunk(old_start, old_count, new_start, new_count, tuple(body)), index


def _range(value: str) -> tuple[int, int]:
    pieces = value.split(",", maxsplit=1)
    try:
        start = int(pieces[0])
        count = int(pieces[1]) if len(pieces) == 2 else 1
    except ValueError as error:
        raise InvalidPatchError("Patch hunk range is malformed.") from error
    if start < 0 or count < 0:
        raise InvalidPatchError("Patch hunk range is invalid.")
    return start, count


def _apply_hunks(original: str, hunks: tuple[_Hunk, ...]) -> str:
    source = original.splitlines(keepends=True)
    output: list[str] = []
    cursor = 0
    for hunk in hunks:
        start = hunk.old_start - 1 if hunk.old_start else 0
        if start < cursor or start > len(source):
            raise InvalidPatchError("Patch hunks are out of order or outside the file.")
        output.extend(source[cursor:start])
        cursor = start
        for line in hunk.lines:
            content = line[1:]
            if line[0] == " ":
                _require_source_line(source, cursor, content)
                output.append(content)
                cursor += 1
            elif line[0] == "-":
                _require_source_line(source, cursor, content)
                cursor += 1
            else:
                output.append(content)
    output.extend(source[cursor:])
    return "".join(output)


def _require_source_line(source: list[str], index: int, expected: str) -> None:
    if index >= len(source) or source[index] != expected:
        raise InvalidPatchError(
            "Patch context does not match the current file content."
        )


def _read_text_bytes(path: Path, workspace: WorkspaceContext) -> bytes:
    limit = workspace.policy.max_file_size_bytes
    try:
        if path.stat().st_size > limit:
            raise FileTooLargeError("Patch target exceeds the configured size limit.")
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError as error:
        raise WorkspaceAccessDeniedError("Patch target cannot be read.") from error
    if len(data) > limit:
        raise FileTooLargeError("Patch target exceeds the configured size limit.")
    if b"\x00" in data:
        raise BinaryFileError("Binary workspace files cannot be patched.")
    _decode_patch_text(data)
    return data


def _decode_patch_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise InvalidTextEncodingError(
            "Patch target is not valid UTF-8 text."
        ) from error


def _encode_like_original(text: str, original: bytes) -> bytes:
    prefix = b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b""
    return prefix + text.encode("utf-8")


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _atomic_replace(path: Path, content: bytes) -> None:
    """Atomically replace one prepared file while preserving its mode bits."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=".oca-patch-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        finally:
            raise
