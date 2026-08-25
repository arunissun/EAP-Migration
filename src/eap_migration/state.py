"""Atomic per-case state and lock management."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from .exceptions import StateError
from .models.common import CaseModel


class FileState(CaseModel):
    sha256: str = Field(min_length=64, max_length=64)
    remote_id: int | None = Field(default=None, gt=0)
    remote_basename: str | None = None
    remote_caption: str | None = None
    remote_response_at: str | None = None


class StateRecord(CaseModel):
    migration_key: str = Field(min_length=1)
    case_sha256: str = Field(min_length=64, max_length=64)
    registration_intent_sha256: str | None = None
    registration_intent_created_at: str | None = None
    registration_recovery_required: bool = False
    registration_request_sha256: str | None = None
    application_request_sha256: str | None = None
    files: dict[str, FileState] = Field(default_factory=dict)
    registration_id: int | None = Field(default=None, gt=0)
    application_kind: str | None = None
    application_id: int | None = Field(default=None, gt=0)
    admin2_ids: list[int] = Field(default_factory=list)
    last_verified_at: str | None = None


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256_bytes(encoded)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _replace_with_bounded_retry(temporary_path: Path, destination: Path) -> None:
    """Handle short Windows antivirus/indexer locks without hiding real failures."""

    for attempt in range(5):
        try:
            os.replace(temporary_path, destination)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


class CaseLock:
    """A conservative exclusive lock; a leftover lock requires operator review."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any = None

    def __enter__(self) -> CaseLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._handle = self.path.open("x", encoding="utf-8")
            self._handle.write(f"pid={os.getpid()}\n")
            self._handle.flush()
        except FileExistsError as exc:
            raise StateError(
                f"Case lock already exists at {self.path}; inspect the other process "
                "before retrying"
            ) from exc
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        if self._handle is not None:
            self._handle.close()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class StateStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, migration_key: str) -> Path:
        return self.root / f"{migration_key}.json"

    def lock_for(self, migration_key: str) -> CaseLock:
        return CaseLock(self.root / f"{migration_key}.lock")

    def load(self, migration_key: str) -> StateRecord | None:
        path = self.path_for(migration_key)
        if not path.exists():
            return None
        try:
            return StateRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StateError(f"State file is unreadable or invalid: {path}") from exc

    def save(self, state: StateRecord) -> Path:
        path = self.path_for(state.migration_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{state.migration_key}.", suffix=".tmp", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(state.model_dump(mode="json"), handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            _replace_with_bounded_retry(temporary_path, path)
        except OSError as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise StateError(f"Could not atomically write state file {path}: {exc}") from exc
        return path

    def reset(self, migration_key: str, *, confirm: bool) -> Path:
        if not confirm:
            raise StateError("State reset requires --confirm")
        path = self.path_for(migration_key)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return path


def atomic_write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_bounded_retry(temporary_path, path)
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        raise StateError(f"Could not atomically write {path}: {exc}") from exc
    return path
