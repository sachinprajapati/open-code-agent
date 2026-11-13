"""Validated, conservative policy for a single read-only workspace."""

from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from pydantic import Field, field_validator

from open_code_agent.protocol.common import ProtocolModel


class WorkspacePolicy(ProtocolModel):
    """Limits and deny rules applied before any model-visible workspace data.

    Filename rules prevent common accidental secret disclosure; they are not a
    secret scanner and cannot prove that an allowed file contains no secret.
    """

    max_file_size_bytes: int = Field(default=1_048_576, gt=0)
    max_read_lines: int = Field(default=500, gt=0)
    max_line_length: int = Field(default=4_000, gt=0)
    max_list_results: int = Field(default=1_000, gt=0)
    max_search_results: int = Field(default=200, gt=0)
    max_output_bytes: int = Field(default=262_144, gt=0)
    max_patch_bytes: int = Field(default=131_072, gt=0)
    max_patch_files: int = Field(default=10, gt=0)
    max_patch_hunks: int = Field(default=100, gt=0)
    max_patch_lines: int = Field(default=5_000, gt=0)
    search_timeout_seconds: float = Field(default=5.0, gt=0)
    include_hidden_files: bool = False
    respect_gitignore: bool = True
    follow_symlinks: bool = False
    excluded_directory_names: list[str] = Field(
        default_factory=lambda: [
            ".git",
            ".hg",
            ".svn",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            "dist",
            "build",
            ".coverage",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "htmlcov",
        ]
    )
    denied_filename_patterns: list[str] = Field(default_factory=list)
    allow_env_templates: bool = True

    @field_validator("excluded_directory_names")
    @classmethod
    def validate_excluded_directory_names(cls, value: list[str]) -> list[str]:
        if any(not name or "/" in name or "\\" in name for name in value):
            raise ValueError("Excluded directory names must be non-empty simple names.")
        return list(value)

    @field_validator("denied_filename_patterns")
    @classmethod
    def validate_denied_patterns(cls, value: list[str]) -> list[str]:
        if any(not pattern.strip() or "\x00" in pattern for pattern in value):
            raise ValueError("Denied filename patterns must be non-empty and NUL-free.")
        return list(value)

    def excludes_directory(self, relative_path: PurePosixPath) -> bool:
        """Return whether any path component is excluded from traversal."""
        return any(
            part in self.excluded_directory_names for part in relative_path.parts
        )

    def is_sensitive(self, relative_path: PurePosixPath) -> bool:
        """Return whether a relative filename is denied without opening it."""
        name = relative_path.name.lower()
        if self.allow_env_templates and name in {
            ".env.example",
            ".env.sample",
            ".env.template",
        }:
            return False
        builtin = (
            name == ".env"
            or name.startswith(".env.")
            or name.endswith(".pem")
            or name.endswith(".key")
            or name in {"id_rsa", "id_ed25519", "credentials", "credentials.json"}
            or (".aws" in relative_path.parts and name == "credentials")
        )
        return builtin or any(
            fnmatchcase(relative_path.as_posix(), pattern) or fnmatchcase(name, pattern)
            for pattern in self.denied_filename_patterns
        )
