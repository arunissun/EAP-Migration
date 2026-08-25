"""Case-file loading and exact environment placeholder expansion."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .exceptions import ConfigurationError
from .models import EapCase, FullCase, SimplifiedCase
from .settings import Settings

ENV_PLACEHOLDER_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


def expand_environment_placeholders(value: Any, environment: dict[str, str]) -> Any:
    """Expand only `${UPPER_CASE_NAME}` placeholders; leave file references intact."""

    if isinstance(value, dict):
        return {
            key: expand_environment_placeholders(item, environment) for key, item in value.items()
        }
    if isinstance(value, list):
        return [expand_environment_placeholders(item, environment) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in environment or not environment[name].strip():
            raise ConfigurationError(f"Environment placeholder ${{{name}}} is not set")
        return environment[name]

    return ENV_PLACEHOLDER_RE.sub(replace, value)


def load_case(path: str | Path, settings: Settings | None = None) -> tuple[EapCase, Path]:
    case_path = Path(path).expanduser().resolve()
    if not case_path.is_file():
        raise ConfigurationError(f"Case file does not exist: {case_path}")

    try:
        raw = json.loads(case_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Case file is not valid JSON: {case_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("Case file root must be a JSON object")

    active_settings = settings or Settings()
    environment = dict(os.environ)
    environment.update(active_settings.contact_environment())
    expanded = expand_environment_placeholders(raw, environment)

    kind = expanded.get("eap_kind")
    adapter: TypeAdapter[EapCase]
    if kind == "simplified":
        adapter = TypeAdapter(SimplifiedCase)
    elif kind == "full":
        adapter = TypeAdapter(FullCase)
    else:
        raise ConfigurationError("Case file must contain eap_kind equal to 'simplified' or 'full'")
    try:
        return adapter.validate_python(expanded), case_path
    except ValidationError as exc:
        raise ConfigurationError(f"Case validation failed for {case_path}: {exc}") from exc


def canonical_case_bytes(case: EapCase) -> bytes:
    """Canonical representation for state-change detection without secrets."""

    return json.dumps(case.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


def legacy_canonical_case_bytes(case: EapCase) -> bytes:
    """Canonical representation used before profile and file-kind metadata existed."""

    payload = case.model_dump(mode="json")
    payload.pop("completeness_profile", None)
    payload["allow_update"] = False
    for spec in payload.get("files", {}).values():
        if isinstance(spec, dict):
            spec.pop("kind", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
