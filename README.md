# OpenCodeAgent

OpenCodeAgent is intended to become a production-quality, open-source, provider-agnostic coding agent. Its Python runtime will eventually support model-provider adapters, repository-aware tools, a secure tool-calling loop, context management, MCP integration, persistence, and a CLI. A thin VS Code extension is planned for a later phase.

## Current status

Completed:

- repository foundation
- provider-neutral protocol
- provider interface and registry
- deterministic mock provider
- OpenAI-compatible Chat Completions transport
- environment-based secret resolution
- streaming text and tool-call normalization
- bounded provider-independent agent loop
- explicit tool runtime and deterministic in-memory development tools
- safe read-only workspace boundary
- `list_files`, `read_file`, and `search_code` workspace tools
- approval-gated patch application for existing workspace files
- approval-gated structured command execution for development checks
- persistent, secret-reference-only OpenAI-compatible provider profiles
- one-shot and interactive CLI coding-agent workflows
- Python stdio JSON-RPC application server with streamed events, cancellable
  in-memory sessions, and bidirectional one-time approvals

The protocol and provider layers provide a stable boundary for future OpenAI,
Anthropic, Gemini, Ollama, and compatible provider adapters.

Not yet implemented: unrestricted shell or terminal access, automatic command
approval, container sandboxing, Git mutation, conversation persistence, the VS Code
extension, MCP, native Anthropic/Gemini adapters, and secure desktop keyring or
VS Code SecretStorage integration. Workspace inspection remains policy-bound;
see [workspace tools](docs/workspace-tools.md) and [command execution](docs/command-execution.md).

## Architecture direction

The source package uses clear boundaries for the future runtime:

- `agent`: orchestration and context management
- `providers`: provider-neutral model interfaces and adapters
- `tools`: repository operations and tool contracts
- `security`: permissions and execution controls
- `persistence`: durable state
- `protocol`: provider-neutral model contracts and future MCP integration
- `api`: future programmatic API

These packages are intentionally empty of implementation until their contracts are designed.

## Contributor installation

OpenCodeAgent requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups
```

## Available commands

```bash
uv run open-code-agent --version
uv run open-code-agent doctor
uv run python -m open_code_agent --version
uv run open-code-agent provider add --profile-id local --display-name Local --base-url http://localhost:1234/v1 --default-model model --api-key-secret-ref env:OCA_API_KEY
uv run open-code-agent provider set-default local
uv run open-code-agent ask "Explain the agent loop" --mode plan
uv run open-code-agent chat
uv run open-code-agent app-server
```

`doctor` is read-only: it only reports local environment details and tool availability.

Provider profiles are stored as versioned JSON in `<data_dir>/profiles.json`.
They contain non-secret endpoint configuration and `env:VARIABLE` references,
never resolved values. A profile ID identifies a saved configuration; its
provider ID is `openai-compatible.<profile_id>`; a model ID identifies a model
at that provider. Writes are atomic and protected from concurrent callers in
one process, but two processes should not modify the same store concurrently.

`ask` and `chat` default `--workspace` to the current directory at the CLI
boundary only. `plan` exposes read tools; `ask` exposes approval-gated patch
and command proposals; `auto-edit` currently keeps patch approval enabled and
always requires approval for commands. Non-interactive approval requests fail
closed. CLI exit statuses are stable: 0 success, 2 configuration, 3 provider,
4 limit, 5 cancellation, and 10 internal failure.

## Configuration

Settings are loaded from `OCA_`-prefixed environment variables:

- `OCA_LOG_LEVEL` (default: `INFO`)
- `OCA_DATA_DIR` (platform-appropriate default; not created on import)
- `OCA_REQUEST_TIMEOUT_SECONDS` (default: `60`)
- `OCA_MAX_AGENT_STEPS` (default: `30`)

## Development checks

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

## Security warning

OpenCodeAgent is not ready to execute model-generated commands. Do not treat this foundation release as a sandbox, a security boundary, or an autonomous coding tool. See [SECURITY.md](SECURITY.md) for vulnerability reporting guidance.

## License

Apache License 2.0. See [LICENSE](LICENSE).
