# Approval-Gated Command Execution

OpenCodeAgent accepts structured command proposals, not shell command strings.
A proposal contains a program name, an ordered argv list, a workspace-relative
working directory, bounded timeout, reason, and policy-approved environment
additions. It is normalized into an immutable snapshot and fingerprinted with
SHA-256. Approval is bound to that exact fingerprint; any changed executable,
argument, directory, timeout, or environment addition requires a new approval.

`run_command` is bound to one `WorkspaceContext`, one application-owned
`CommandExecutionPolicy`, one approval handler, and one process runner. A
model cannot select a new workspace or replace any of those authorities. With
no policy or approval handler it fails closed. Approval is always one-time;
there is no “always approve” option.

## Review and classification

The CLI shows command argv (for display only), directory, reason, timeout,
classification, risk, possible file/network effects, and fingerprint before it
starts a process. The default response is deny. Non-interactive CLI use denies
the command. Decline, cancellation, approval errors, or fingerprint mismatch
create no process.

The deterministic classifier recognizes safe Git reads (`status`, `diff`,
`log`, `show`, selected `branch`/`rev-parse`), `pytest`, `ruff check`, `ruff
format --check`, `mypy`, and constrained `uv run` verification commands.
Tests and linters still need approval: repository configuration, plugins, and
test code can execute arbitrary local code. Unknown commands are high risk;
shells, inline interpreters, network tools, package changes, privilege
escalation, destructive filesystem operations, and Git mutation commands are
blocked by default. This classification is a safety aid, not semantic proof.

For example, an approved focused test proposal is:

```json
{"executable":"uv","arguments":["run","pytest","tests/unit/agent","-q"],"working_directory":".","timeout_seconds":120,"reason":"Verify the agent change"}
```

`rm -rf build` is classified as destructive and is blocked without showing an
approval prompt.

## Process boundary

Execution uses `asyncio.create_subprocess_exec` with individual argv entries;
it never invokes a shell. Shell programs are blocked, and shell-looking
characters in an ordinary argv value stay literal rather than becoming command
substitution, pipes, redirects, glob expansion, or operators. The working directory is resolved again
inside the workspace immediately before execution. The child receives a small
allowlist of inherited platform variables (normally `PATH`, home/temp values,
and Windows system essentials where needed), plus policy-approved additions.
API keys, tokens, cloud credentials, database URLs, SSH-agent variables, and
other ambient variables are not inherited or shown in results.

Stdout and stderr are drained concurrently and retained under separate,
combined, and line-length limits. Results report truncation, exit status,
duration, timeout, and only workspace-relative paths. Timeout and cancellation
terminate then reap the child; on POSIX a new process session permits
best-effort process-group termination. Other platforms can only guarantee
best-effort direct-child termination.

This feature is not a complete OS sandbox and has no container isolation.
Repository code and local concurrent processes remain outside its control.
