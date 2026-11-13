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

The protocol and provider layers provide a stable boundary for future OpenAI,
Anthropic, Gemini, Ollama, and compatible provider adapters.

Not yet implemented: interactive provider configuration CLI/UI, the agent loop,
repository/file tools, file editing, terminal execution, the VS Code extension,
native Anthropic/Gemini adapters, secure desktop keyring or VS Code
SecretStorage integration, and MCP. No commands or file edits can be executed
yet; this remains infrastructure, not a functional coding agent.

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
```

`doctor` is read-only: it only reports local environment details and tool availability.

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
