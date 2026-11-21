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

Current MVP limitations: only OpenAI-compatible chat-completions endpoints,
no bundled runtime, persistent conversations, multi-root sessions,
multiple concurrent chats, Marketplace publishing, MCP, or native
Anthropic/Gemini adapters.
