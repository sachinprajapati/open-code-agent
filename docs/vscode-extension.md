# VS Code extension MVP

`vscode-extension/` is a thin, development-only VS Code client for the Python
stdio app server. Python remains responsible for provider calls, agent logic,
workspace policy, tools, approvals, and command execution. The extension owns
only process supervision, JSON-RPC framing, session presentation, and explicit
one-time approval UI.

## Development

Install the Python environment and extension dependencies:

```bash
uv sync --all-groups
cd vscode-extension
npm ci
npm run protocol:check
npm run lint && npm run typecheck && npm test && npm run compile
```

Open either the repository root or `vscode-extension/` in VS Code and run the
**Run OpenCodeAgent Extension** launch configuration (F5). It builds
`vscode-extension/`, opens the repository root in an Extension Development
Host, and uses its workspace settings for the Python runtime. Then open the
OpenCodeAgent activity-bar view. Set breakpoints in
`vscode-extension/src/**/*.ts`; source maps map them to the bundled extension
code. The default runtime command is `open-code-agent app-server`. For local
development, configure `openCodeAgent.server.executable` as `uv` and
`openCodeAgent.server.arguments` as `['run', 'open-code-agent', 'app-server']`.
No runtime is bundled or downloaded.

The extension uses VS Code's primary workspace folder per in-memory agent
session. Changing folders invalidates the session. Profiles are listed from the existing Python
profile store; create or update profiles only with `open-code-agent provider`.
Provider profiles can be managed in the sidebar.  The Python profile registry
remains the source of the provider name, endpoint, model, and timeout; the
extension stores an API key separately in VS Code SecretStorage under a
profile-ID namespaced key.  A profile response contains only whether
authentication is configured—never the key or its secret reference.  Empty
key fields preserve an existing key; replacing a key is explicit and deleting
a profile deletes its corresponding SecretStorage entry.  Connection tests and
model refreshes use the selected profile.  If an endpoint lacks `/models`, use
a manually entered model name.

## Operation and safety

The process is launched with Node `spawn(executable, arguments)` and
`shell: false`. Executable configuration is taken solely from trusted VS Code
settings, never from the webview or model. stdout is newline-delimited JSON-RPC
only; stderr is routed to the **OpenCodeAgent** Output channel. The host
initializes with protocol `1.0`, verifies required capabilities, and sends
`shutdown` then `exit` on deactivation before bounded termination.

Plan, Ask, and Auto-edit map to the Python modes (Auto-edit is sent as
`auto_edit`). Patch and command proposals appear as one-time approval cards.
They default to denial, have no approve-always action, and are denied on webview
disposal, runtime restart/disconnect, cancellation, or stale correlation. Cancel
sends `turn/cancel` and waits for a terminal event.

The webview has a nonce CSP, only loads its bundled script/style, uses
`textContent` for untrusted agent content, and accepts a small validated message
union. It cannot access Node, file APIs, process configuration, arbitrary VS
Code commands, or arbitrary JSON-RPC methods.

## Settings and troubleshooting

Settings include `openCodeAgent.server.executable`, `.arguments`, `.cwd`,
`.startupTimeoutMs`, `.shutdownTimeoutMs`, `.logLevel`, `.defaultProfile`,
`.defaultModel`, `.defaultMode`, `.maxRestarts`, and `.debug`. GUI-launched VS
Code may not inherit the shell environment containing an `env:VARIABLE` API-key
reference; launch VS Code from an environment that has it or configure it in
the OS session. If the runtime is not found, set executable/arguments then run
**OpenCodeAgent: Restart Runtime** and inspect the Output channel.

For persistent local diagnostics, the extension also writes lifecycle and RPC
metadata (never prompt text or secrets) to
`.open-code-agent/extension.log` in the primary workspace folder.

## Manual MCP acceptance

Use the local development fixture at `examples/mcp/demo_server.py` to test the
full MCP path through an Extension Development Host. In the **MCP Servers** UI,
add a server with Command `uv`, Arguments `run python examples/mcp/demo_server.py`,
Working directory set to the repository root, and no environment variables.

The fixture is deliberately harmless: it exposes exactly `echo(message)`,
`add_numbers(left, right)`, and `server_info()`. It does not provide shell,
filesystem, environment, network, subprocess, or secret access.

1. Connect the configured server and confirm it appears as **Connected**.
2. Confirm exactly three tools are discovered: `echo`, `add_numbers`, and
   `server_info`.
3. Ask the agent to use one of those tools and confirm it proposes a tool call.
4. Confirm the approval card identifies the server, tool, and arguments.
5. Reject the proposal and confirm the tool does not run.
6. Repeat the proposal, approve it once, and confirm the returned result is
   displayed (for example, `echo: hello` for `echo("hello")`).
7. Disable or delete the server, then confirm the agent cannot make further
   calls to its tools.
8. Close the Extension Development Host and confirm its demo server process is
   terminated.

Current MVP limitations: only OpenAI-compatible chat-completions endpoints,
no bundled runtime, persistent conversations, multi-root sessions,
multiple concurrent chats, Marketplace publishing, or native
Anthropic/Gemini adapters.
