"""Structured logging helpers that redact credentials and authorization headers."""

from __future__ import annotations

import json
import logging as stdlib_logging
import sys
from collections.abc import Mapping
from typing import Any

SENSITIVE_KEYS = {
    "authorization",
    "api_token",
    "token",
    "password",
    "secret",
    "access_token",
}


def redact(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    """Return a JSON-safe value with known secret fields and values redacted."""

    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if str(key).lower() in SENSITIVE_KEYS else redact(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        result = value
        for secret in secrets:
            if secret:
                result = result.replace(secret, "[REDACTED]")
        if result.lower().startswith("token "):
            return "Token [REDACTED]"
        return result
    return value


class JsonFormatter(stdlib_logging.Formatter):
    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.secrets = secrets

    def format(self, record: stdlib_logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event_data = record.__dict__.get("event_data")
        if event_data is not None:
            payload["data"] = redact(event_data, self.secrets)
        return json.dumps(redact(payload, self.secrets), default=str, sort_keys=True)


class RedactingLoggerAdapter(stdlib_logging.LoggerAdapter[stdlib_logging.Logger]):
    def __init__(self, logger: stdlib_logging.Logger, secrets: tuple[str, ...]) -> None:
        super().__init__(logger, {})
        self.secrets = secrets

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        extra = kwargs.setdefault("extra", {})
        event_data = extra.get("event_data")
        if event_data is not None:
            extra["event_data"] = redact(event_data, self.secrets)
        return msg, kwargs


def configure_logging(secrets: tuple[str, ...] = ()) -> RedactingLoggerAdapter:
    logger = stdlib_logging.getLogger("eap_migration")
    logger.setLevel(stdlib_logging.INFO)
    logger.handlers.clear()
    handler = stdlib_logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter(secrets))
    logger.addHandler(handler)
    logger.propagate = False
    return RedactingLoggerAdapter(logger, secrets)
