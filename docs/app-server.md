# Application Server

`open-code-agent app-server` is a local editor-facing application server. It
uses standard input and output rather than a TCP listener: one UTF-8 JSON object
per line, with stdout reserved exclusively for JSON-RPC 2.0 messages. This makes
the editor the process owner and avoids exposing a network service.

## Handshake and lifecycle

The first request must be `initialize`, with `client_name`, `client_version`, and
`supported_protocol_versions`. Version `1.0` is currently supported. The result
describes actual capabilities, method names, and enforced limits. The optional
`initialized` notification may follow. Stateful methods are rejected before the
handshake. Send `shutdown` to stop work and close resources, then `exit`; EOF
also performs the same cleanup. No protocol message is emitted after `exit`.

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"client_name":"example","client_version":"0.1","supported_protocol_versions":["1.0"]}}
```

## Methods and sessions

`profiles/list`, `profiles/get`, `profiles/test`, and `models/list` expose the
existing secret-reference-only profile store. Profile responses state whether
authentication is configured but never include secret references or resolved
keys. Temporary providers used for tests/model listing are closed immediately.

`session/create` accepts a stored profile (or default), a trusted IDE workspace
path, mode (`plan`, `ask`, or `auto_edit`), optional model, and bounded limits.
It creates a random in-memory session ID. `session/status`, `session/list`,
`session/clear`, and `session/close` operate only on that connection's sessions.
Conversation history is committed only after a completed turn and is never
persisted or resumed after process restart.

`turn/start` returns immediately with a run ID. The server streams `agent/event`
notifications with unique increasing sequence values per run. Text deltas retain
order; reasoning is represented only as activity, never content. Tool output is
bounded and terminal events contain safe summaries. `turn/cancel` cancels only
the specified active session run and preserves earlier committed history.

## Approvals and safety

Patch and command tools use the existing one-time approval system. The server
sends `approval/request` as a JSON-RPC request and waits for the corresponding
client response. Responses must echo `approval_id` and the proposal fingerprint:

```json
{"jsonrpc":"2.0","id":"server:approval:1","result":{"approval_id":"server:approval:1","fingerprint":"placeholder","decision":"approved_once"}}
```

Unknown, mismatched, duplicate, expired, disconnected, or cancelled approvals
fail closed. Blocked commands never ask for approval, and plan sessions contain
no edit or command tools. JSON-RPC parse/request errors use standard codes;
application errors use the documented `-32001` through `-32013` reserved range.
No hidden reasoning, headers, environment, API keys, absolute workspace paths in
events, process handles, or Python tracebacks are placed on the protocol.

## Limits and manifest

Input/output message sizes, sessions, pending approval requests, concurrent
runs, event size, approval timeout, and shutdown timeout are bounded. The
machine-readable manifest is [app-server.protocol.json](app-server.protocol.json).
Regenerate it with:

```bash
uv run open-code-agent app-server-manifest
```

A VS Code client should spawn `open-code-agent app-server`, write only JSON lines
to its stdin, parse only JSON lines from stdout, route server requests while it
continues reading events, then send `shutdown` followed by `exit` before waiting
for process termination. This server intentionally has no HTTP, TCP, WebSocket,
MCP, PTY, remote execution, or conversation persistence support.
