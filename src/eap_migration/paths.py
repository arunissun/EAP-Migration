"""Deterministic repository, state, and artifact path resolution."""

from __future__ import annotations

from pathlib import Path

from .exceptions import ConfigurationError


def find_repository_root(path: str | Path) -> Path:
    """Find this project's root without depending on the process CWD."""

    resolved = Path(path).expanduser().resolve()
    start = resolved if resolved.is_dir() else resolved.parent
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file() and (
            candidate / "src" / "eap_migration"
        ).is_dir():
            return candidate
    raise ConfigurationError(
        f"Could not find the eap-migration repository root above {resolved}; "
        "expected pyproject.toml and src/eap_migration"
    )


def default_state_root(repository_root: Path) -> Path:
    return repository_root / ".state"


def default_artifact_root(repository_root: Path) -> Path:
    return repository_root / "artifacts"


def legacy_state_path(case_path: str | Path, migration_key: str) -> Path:
    resolved_case = Path(case_path).expanduser().resolve()
    return resolved_case.parent / ".state" / f"{migration_key}.json"
