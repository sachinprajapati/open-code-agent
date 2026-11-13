"""Structured proposal and deterministic command classifier tests."""

from dataclasses import FrozenInstanceError

import pytest

from open_code_agent.execution import (
    CommandCategory,
    CommandClassifier,
    CommandProposal,
    CommandRisk,
    InvalidCommandProposalError,
)


def proposal(executable: str, arguments: list[str] | None = None) -> CommandProposal:
    return CommandProposal.create(
        executable=executable,
        arguments=arguments or [],
        working_directory=".",
        timeout_seconds=10,
        reason="run a focused check",
    )


def test_proposal_is_normalized_immutable_and_fingerprinted() -> None:
    first = proposal("pytest", ["tests/unit"])
    same = proposal("pytest", ["tests/unit"])
    changed = proposal("pytest", ["tests/integration"])

    assert first.arguments == ("tests/unit",)
    assert first.fingerprint == same.fingerprint
    assert first.fingerprint != changed.fingerprint
    with pytest.raises(FrozenInstanceError):
        first.executable = "ruff"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kwargs",),
    [
        ({"executable": ""},),
        ({"executable": "bash -c"},),
        ({"executable": "pytest\n"},),
        ({"arguments": ["\x00"]},),
        ({"working_directory": "../outside"},),
        ({"timeout_seconds": 0},),
        ({"reason": " "},),
    ],
)
def test_invalid_proposals_are_rejected(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "executable": "pytest",
        "arguments": [],
        "working_directory": ".",
        "timeout_seconds": 1,
        "reason": "check",
    }
    values.update(kwargs)
    with pytest.raises(InvalidCommandProposalError):
        CommandProposal.create(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("executable", "arguments", "category", "risk"),
    [
        ("git", ["status"], CommandCategory.SAFE_READ, CommandRisk.LOW),
        ("git", ["reset", "--hard"], CommandCategory.DESTRUCTIVE, CommandRisk.BLOCKED),
        ("git", ["push", "--force"], CommandCategory.DESTRUCTIVE, CommandRisk.BLOCKED),
        ("pytest", [], CommandCategory.TEST, CommandRisk.MEDIUM),
        ("uv", ["run", "pytest"], CommandCategory.TEST, CommandRisk.MEDIUM),
        (
            "uv",
            ["run", "python", "x.py"],
            CommandCategory.INTERPRETER,
            CommandRisk.BLOCKED,
        ),
        ("ruff", ["check", "."], CommandCategory.LINT, CommandRisk.LOW),
        ("ruff", ["format", "--check", "."], CommandCategory.FORMAT, CommandRisk.LOW),
        ("ruff", ["format", "."], CommandCategory.WORKSPACE_WRITE, CommandRisk.HIGH),
        ("mypy", ["src"], CommandCategory.LINT, CommandRisk.LOW),
        ("pre-commit", ["run"], CommandCategory.LINT, CommandRisk.MEDIUM),
        ("pip", ["install", "x"], CommandCategory.DEPENDENCY_CHANGE, CommandRisk.HIGH),
        ("bash", ["-c", "true"], CommandCategory.SHELL, CommandRisk.BLOCKED),
        ("python", ["-c", "pass"], CommandCategory.INTERPRETER, CommandRisk.BLOCKED),
        (
            "curl",
            ["https://example.invalid"],
            CommandCategory.NETWORK,
            CommandRisk.BLOCKED,
        ),
        ("rm", ["file"], CommandCategory.DESTRUCTIVE, CommandRisk.BLOCKED),
        ("sudo", ["true"], CommandCategory.PRIVILEGE_ESCALATION, CommandRisk.BLOCKED),
        ("unfamiliar", [], CommandCategory.UNKNOWN, CommandRisk.HIGH),
    ],
)
def test_classifier_examples(
    executable: str,
    arguments: list[str],
    category: CommandCategory,
    risk: CommandRisk,
) -> None:
    classified = CommandClassifier().classify(proposal(executable, arguments))
    assert classified.category is category
    assert classified.risk is risk
