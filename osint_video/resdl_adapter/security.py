"""Filesystem safety policy for downloaded media."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class PathPolicyError(ValueError):
    """Raised when a requested output path escapes configured roots."""


@dataclass(frozen=True)
class PathPolicy:
    """Allowlist of directories in which res-downloader may write files."""

    allowed_roots: tuple[Path, ...]

    def __post_init__(self) -> None:
        roots = tuple(Path(root).expanduser().resolve() for root in self.allowed_roots)
        if not roots:
            raise PathPolicyError("at least one allowed root is required")
        object.__setattr__(self, "allowed_roots", roots)

    def validate(self, requested: str | Path) -> Path:
        """Resolve and validate a path using strict containment semantics."""
        candidate = Path(requested).expanduser().resolve(strict=False)
        for root in self.allowed_roots:
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            return candidate
        raise PathPolicyError(f"path is outside configured download roots: {candidate.name}")

    def validate_directory(self, requested: str | Path) -> Path:
        """Validate a directory and create it only after containment succeeds."""
        path = self.validate(requested)
        path.mkdir(parents=True, exist_ok=True)
        return path
