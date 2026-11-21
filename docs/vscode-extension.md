# OpenCodeAgent VS Code extension

`vscode-extension/` is a thin VS Code client for the Python stdio app server.
Installed target VSIX files include a native frozen runtime. Python remains responsible for provider calls, agent logic,
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
Extension Development Host mode continues to use this configurable Python/uv
launcher. Packaged installs use the executable shipped inside the matching
platform VSIX and ignore those development launcher settings.

## Installing a packaged build

Install VS Code and the VSIX matching the computer's operating system and CPU:

| Computer | Artifact |
| --- | --- |
| Linux x64 | `open-code-agent-linux-x64.vsix` |
| macOS Apple silicon | `open-code-agent-darwin-arm64.vsix` |
| macOS Intel | `open-code-agent-darwin-x64.vsix` |
| Windows x64 | `open-code-agent-win32-x64.vsix` |

In VS Code, run **Extensions: Install from VSIX...** and select the downloaded
file. VS Code is the only runtime prerequisite; Python and uv are not needed.
Each artifact includes just its matching executable under
`runtime/<target>/open-code-agent` (or `.exe` on Windows). Provider API keys
remain in VS Code SecretStorage. Runtime profiles are created under the normal
per-user OpenCodeAgent data directory, never in the extension installation.

Run **OpenCodeAgent: Runtime Diagnostics** from the Command Palette to see the
extension version, detected platform and architecture, runtime mode and path,
and runtime health. Startup errors are also written to the OpenCodeAgent
Output channel. If VS Code reports a missing or unsupported runtime, install
the VSIX for the exact OS/architecture shown in diagnostics.

## Building and verifying packages

The native runtime is built with PyInstaller as a dedicated app-server-only
executable. PyInstaller is used because it freezes the locked Python app and
its dependencies into a self-contained executable without including a Python
installation. Native builds must run on the matching operating system and CPU;
cross-platform binaries are not produced.

On a native build host, from `vscode-extension/`:

```bash
npm ci
npm run compile
npm run build:runtime
node scripts/package.mjs linux-x64 # replace with this host's target
```

The convenience commands `package:linux-x64`, `package:darwin-arm64`,
`package:darwin-x64`, and `package:win32-x64` each build and package for the
current host; the target must match. `package:all` packages four previously
built native runtimes and fails if any are missing. `verify:package` validates
that the executable and target metadata are present and rejects common cache,
test, profile, and credential paths. Packaging stages only the extension
manifest, compiled code, media, license, README, and matching runtime.

Packages and SHA-256 sidecars are written to `artifacts/`. Verify a downloaded
artifact with `sha256sum -c open-code-agent-linux-x64.vsix.sha256` (or the
platform's equivalent), then install it from VS Code. The GitHub Actions
`Extension packages` workflow builds the Linux x64, macOS arm64, macOS x64, and
Windows x64 artifacts on native runners and uploads them as CI artifacts. It
does not publish to the Marketplace or create a GitHub Release.

## Limitations and troubleshooting

Native builds require a matching target runner and the Python/uv build toolchain;
users of packaged VSIX files need only VS Code. An unavailable platform/CPU has
no bundled fallback and requires a matching artifact. A startup failure should
include the process error in the Output channel; use Runtime Diagnostics to
check the resolved path and binary health. Antivirus quarantine or a damaged
download can also prevent the executable from starting; verify its checksum
and reinstall the VSIX. Automatic Marketplace publishing is not part of this
phase.

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
persistent conversations, multi-root sessions,
multiple concurrent chats, Marketplace publishing, or native
Anthropic/Gemini adapters.
