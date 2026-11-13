# Contributing to OpenCodeAgent

Thank you for helping build OpenCodeAgent. This repository is in its foundation phase, so prefer small, well-tested changes that establish clear contracts over speculative feature scaffolding.

## Local setup

Install Python 3.12+ and [uv](https://docs.astral.sh/uv/), then create the development environment:

```bash
uv sync --all-groups
```

Optionally install the configured hooks:

```bash
uv run pre-commit install
```

## Before opening a pull request

Run all checks from the repository root:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest
```

Add focused tests for behavior changes and update the documentation when users or contributors need to know about the change. Keep model APIs, command execution, and repository-editing behavior out of this foundation phase unless the project scope explicitly changes.

## Pull requests

Describe the problem, the implementation, and verification performed. Keep each pull request narrowly scoped, avoid unrelated formatting changes, and never commit credentials, tokens, or private repository contents.
