"""Domain exceptions with safe, operator-facing messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class EapMigrationError(Exception):
    """Base class for expected migration failures."""


class ConfigurationError(EapMigrationError):
    """The local environment or case configuration is not usable."""


class ValidationFailure(EapMigrationError):
    """The case cannot be safely migrated."""


class SafetyError(EapMigrationError):
    """A command would violate the migration safety boundary."""


class StateError(EapMigrationError):
    """State is missing, stale, conflicted, or otherwise unsafe to use."""


class RecoveryRequired(EapMigrationError):
    """An ambiguous write outcome needs operator review before continuing."""


@dataclass(slots=True)
class ApiError(EapMigrationError):
    """An HTTP error whose body has already been redacted."""

    method: str
    url: str
    status_code: int
    response_body: Any
    correlation_id: str | None = None

    def __str__(self) -> str:
        suffix = f"; correlation_id={self.correlation_id}" if self.correlation_id else ""
        return (
            f"GO API {self.method} {self.url} returned HTTP {self.status_code}: "
            f"{self.response_body}{suffix}"
        )
