"""GET-only checks that the local writable contract still matches OpenAPI."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .exceptions import ValidationFailure
from .models.full import FullEapApplication
from .models.simplified import SimplifiedEapApplication


def _schema(document: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    components = document.get("components")
    schemas = components.get("schemas") if isinstance(components, Mapping) else None
    value = schemas.get(name) if isinstance(schemas, Mapping) else None
    if not isinstance(value, Mapping):
        raise ValidationFailure(f"OpenAPI contract is missing components.schemas.{name}")
    return value


def _property(schema: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    properties = schema.get("properties")
    value = properties.get(name) if isinstance(properties, Mapping) else None
    if not isinstance(value, Mapping):
        raise ValidationFailure(f"OpenAPI contract is missing writable property '{name}'")
    return value


def validate_openapi_contract(document: Any, eap_kind: str) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        raise ValidationFailure("OpenAPI endpoint did not return a JSON object")
    if eap_kind not in {"simplified", "full"}:
        raise ValidationFailure(f"Unsupported EAP kind for OpenAPI contract: {eap_kind}")
    application_name = "SimplifiedEAP" if eap_kind == "simplified" else "FullEAP"
    patched_name = f"Patched{application_name}"
    application_schema = _schema(document, application_name)
    patched_schema = _schema(document, patched_name)
    local_model = (
        SimplifiedEapApplication if eap_kind == "simplified" else FullEapApplication
    )
    properties = application_schema.get("properties")
    if not isinstance(properties, Mapping):
        raise ValidationFailure(f"OpenAPI schema {application_name} has no properties")
    missing = sorted(
        name
        for name in local_model.model_fields
        if name not in properties
    )
    if missing:
        raise ValidationFailure(
            f"OpenAPI drift: {application_name} is missing local writable fields: {missing}"
        )
    caption = _property(_schema(document, "EAPFile"), "caption")
    if caption.get("maxLength") != 225:
        raise ValidationFailure(
            "OpenAPI drift: EAPFile.caption maxLength is "
            f"{caption.get('maxLength')!r}, expected 225"
        )
    modified_at = _property(patched_schema, "modified_at")
    if modified_at.get("type") != "string":
        raise ValidationFailure(
            "OpenAPI drift: patched application modified_at is not a string"
        )
    paths = document.get("paths")
    detail_path = f"/api/v2/{eap_kind}-eap/{{id}}/"
    detail = paths.get(detail_path) if isinstance(paths, Mapping) else None
    if not isinstance(detail, Mapping) or "patch" not in detail:
        raise ValidationFailure(f"OpenAPI drift: PATCH is missing at {detail_path}")
    return {
        "openapi": document.get("openapi"),
        "api_version": document.get("info", {}).get("version")
        if isinstance(document.get("info"), Mapping)
        else None,
        "application_schema": application_name,
        "patched_schema": patched_name,
        "local_fields_checked": len(local_model.model_fields),
        "caption_max_length": caption.get("maxLength"),
        "modified_at_type": modified_at.get("type"),
        "patch_path": detail_path,
    }
