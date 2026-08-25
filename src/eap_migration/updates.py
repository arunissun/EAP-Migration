"""Explicit, reviewed PATCH updates for unlocked Under Development EAPs."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from .catalogs import fetch_reference_catalog
from .contracts import validate_openapi_contract
from .exceptions import (
    ConfigurationError,
    RecoveryRequired,
    SafetyError,
    StateError,
    ValidationFailure,
)
from .files import validate_application_file_payload
from .logging import redact
from .settings import STAGE_ORIGIN
from .state import atomic_write_json, payload_sha256, utc_now
from .verification import compare_expected_payload
from .word_limits import validate_narrative_limits

EapKind = Literal["simplified", "full"]

DETAIL_PATHS: dict[str, str] = {
    "simplified": "/simplified-eap/{id}/",
    "full": "/full-eap/{id}/",
}

TOP_LEVEL_SERVER_FIELDS = {
    "id",
    "created_at",
    "created_by",
    "created_by_details",
    "modified_by",
    "modified_by_details",
    "modified_at",
    "version",
    "is_locked",
    "eap_registration",
}


class UpdateChangeDocument:
    """Small user-authored update document; values are validated by the planner."""

    def __init__(self, changes: dict[str, Any]) -> None:
        if not changes:
            raise ValidationFailure("Update change document must contain at least one change")
        self.changes = changes


def load_change_document(path: str | Path) -> UpdateChangeDocument:
    source = Path(path).expanduser().resolve()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"Update change document is unreadable: {source}") from exc
    if not isinstance(raw, Mapping) or not isinstance(raw.get("changes"), Mapping):
        raise ConfigurationError(
            f"Update change document must be an object with a 'changes' object: {source}"
        )
    return UpdateChangeDocument(dict(raw["changes"]))


def _server_field(name: str, *, top_level: bool = False) -> bool:
    if name == "previous_id" or name.endswith("_details") or name.endswith("_display"):
        return True
    if name in {
        "created_at",
        "created_by",
        "modified_at",
        "modified_by",
        "is_locked",
        "version",
    }:
        return True
    return top_level and name in TOP_LEVEL_SERVER_FIELDS


def writable_payload(remote: Mapping[str, Any]) -> dict[str, Any]:
    """Remove server-owned response fields while retaining nested IDs for PATCH."""

    def clean(value: Any, *, top_level: bool = False) -> Any:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if _server_field(str(key), top_level=top_level):
                    continue
                if key == "file" and not top_level and isinstance(item, str):
                    continue
                result[str(key)] = clean(item)
            return result
        if isinstance(value, list):
            return [clean(item) for item in value]
        return copy.deepcopy(value)

    return clean(remote, top_level=True)


def _assert_writable_top_level(payload: Mapping[str, Any], label: str) -> None:
    forbidden = sorted(
        str(name) for name in payload if _server_field(str(name), top_level=True)
    )
    if forbidden:
        raise ValidationFailure(f"{label} contains server-owned field(s): {forbidden}")


def _require_change_fields(operation: Mapping[str, Any], field: str) -> None:
    allowed = {"set", "add", "remove", "update", "replace"}
    unknown = sorted(set(operation) - allowed)
    if unknown:
        raise ValidationFailure(f"Update for '{field}' has unsupported operation(s): {unknown}")
    if "set" in operation and len(operation) != 1:
        raise ValidationFailure(
            f"Update for '{field}' cannot combine set with list operations"
        )
    if "replace" in operation and len(operation) != 1:
        raise ValidationFailure(
            f"Update for '{field}' cannot combine replace with other operations"
        )
    if not operation:
        raise ValidationFailure(f"Update for '{field}' has no operation")


def _find_unique_item(items: list[Any], selector: Any, field: str) -> int:
    if not isinstance(selector, Mapping) or not selector:
        raise ValidationFailure(
            f"Update for '{field}' needs a non-empty object selector; use a stable id or key"
        )
    matches = [
        index
        for index, item in enumerate(items)
        if isinstance(item, Mapping)
        and all(item.get(str(key)) == value for key, value in selector.items())
    ]
    if not matches:
        raise ValidationFailure(
            f"Update for '{field}' could not find item matching {dict(selector)}"
        )
    if len(matches) > 1:
        raise ValidationFailure(
            f"Update for '{field}' selector {dict(selector)} matched multiple items"
        )
    return matches[0]


def _validate_new_item(item: Any, field: str) -> None:
    if not isinstance(item, Mapping):
        raise ValidationFailure(f"New item for '{field}' must be an object")
    if "id" in item or "previous_id" in item:
        raise ValidationFailure(
            f"New item for '{field}' must not contain id or previous_id; the API assigns the ID"
        )
    if any(_server_field(str(key)) for key in item):
        raise ValidationFailure(f"New item for '{field}' contains server-owned fields")


def apply_changes(remote: Mapping[str, Any], changes: Mapping[str, Any]) -> dict[str, Any]:
    """Apply explicit changes to a remote response and return the final response shape."""

    final = copy.deepcopy(dict(remote))
    for field, raw_operation in changes.items():
        if not isinstance(field, str) or not field:
            raise ValidationFailure("Update field names must be non-empty strings")
        if _server_field(field, top_level=True):
            raise ValidationFailure(f"Update cannot change server-owned field '{field}'")
        if field not in final:
            raise ValidationFailure(f"Update field '{field}' does not exist on the remote EAP")
        if not isinstance(raw_operation, Mapping):
            raise ValidationFailure(f"Update for '{field}' must be an operation object")
        operation = dict(raw_operation)
        _require_change_fields(operation, field)
        if "set" in operation:
            final[field] = copy.deepcopy(operation["set"])
            continue
        if not isinstance(final[field], list):
            raise ValidationFailure(f"List operation for '{field}' requires a remote list")
        items = copy.deepcopy(final[field])
        if "replace" in operation:
            if not isinstance(operation["replace"], list):
                raise ValidationFailure(f"Replacement for '{field}' must be a list")
            items = copy.deepcopy(operation["replace"])
            final[field] = items
            continue
        if "remove" in operation:
            selectors = operation["remove"]
            if not isinstance(selectors, list):
                raise ValidationFailure(f"Remove operation for '{field}' must be a list")
            for selector in selectors:
                index = _find_unique_item(items, selector, field)
                items.pop(index)
        if "update" in operation:
            updates = operation["update"]
            if not isinstance(updates, list):
                raise ValidationFailure(f"Update operation for '{field}' must be a list")
            for update in updates:
                if not isinstance(update, Mapping):
                    raise ValidationFailure(f"Each item update for '{field}' must be an object")
                selector = update.get("match")
                values = update.get("set")
                if not isinstance(values, Mapping) or not values:
                    raise ValidationFailure(
                        f"Each item update for '{field}' needs a non-empty set object"
                    )
                if any(_server_field(str(key)) for key in values):
                    raise ValidationFailure(
                        f"Item update for '{field}' contains server-owned fields"
                    )
                index = _find_unique_item(items, selector, field)
                if not isinstance(items[index], Mapping):
                    raise ValidationFailure(f"Existing item for '{field}' is not an object")
                items[index].update(copy.deepcopy(dict(values)))
        if "add" in operation:
            additions = operation["add"]
            if not isinstance(additions, list):
                raise ValidationFailure(f"Add operation for '{field}' must be a list")
            for item in additions:
                _validate_new_item(item, field)
                items.append(copy.deepcopy(item))
        final[field] = items
    return final


@dataclass(slots=True)
class UpdatePlan:
    application_id: int
    eap_kind: EapKind
    registration_id: int
    base_modified_at: str
    base_version: int | None
    base_payload: dict[str, Any]
    final_payload: dict[str, Any]
    patch_payload: dict[str, Any]
    changes: dict[str, Any]
    differences: list[dict[str, Any]]
    base_payload_sha256: str
    final_payload_sha256: str
    patch_payload_sha256: str
    catalog_summary: dict[str, Any]
    contract_summary: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "application_id": self.application_id,
            "eap_kind": self.eap_kind,
            "registration_id": self.registration_id,
            "base_modified_at": self.base_modified_at,
            "base_version": self.base_version,
            "base_payload": self.base_payload,
            "final_payload": self.final_payload,
            "patch_payload": self.patch_payload,
            "changes": self.changes,
            "differences": self.differences,
            "base_payload_sha256": self.base_payload_sha256,
            "final_payload_sha256": self.final_payload_sha256,
            "patch_payload_sha256": self.patch_payload_sha256,
            "reference_catalog": self.catalog_summary,
            "openapi_contract": self.contract_summary,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> UpdatePlan:
        try:
            raw_kind = value["eap_kind"]
            if raw_kind not in DETAIL_PATHS:
                raise ValidationFailure(f"Update plan has unsupported eap_kind={raw_kind!r}")
            application_id = value["application_id"]
            registration_id = value["registration_id"]
            if (
                not isinstance(application_id, int)
                or isinstance(application_id, bool)
                or application_id <= 0
                or not isinstance(registration_id, int)
                or isinstance(registration_id, bool)
                or registration_id <= 0
            ):
                raise ValidationFailure("Update plan target IDs must be positive integers")
            if not isinstance(value["base_modified_at"], str) or not value["base_modified_at"]:
                raise ValidationFailure("Update plan base_modified_at must be a non-empty string")
            plan = cls(
                application_id=application_id,
                eap_kind=cast(EapKind, raw_kind),
                registration_id=registration_id,
                base_modified_at=value["base_modified_at"],
                base_version=value.get("base_version"),
                base_payload=dict(value["base_payload"]),
                final_payload=dict(value["final_payload"]),
                patch_payload=dict(value["patch_payload"]),
                changes=dict(value["changes"]),
                differences=list(value.get("differences", [])),
                base_payload_sha256=value["base_payload_sha256"],
                final_payload_sha256=value["final_payload_sha256"],
                patch_payload_sha256=value["patch_payload_sha256"],
                catalog_summary=dict(value.get("reference_catalog", {})),
                contract_summary=dict(value.get("openapi_contract", {})),
                created_at=value["created_at"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailure("Update plan has an invalid shape") from exc
        if plan.eap_kind not in DETAIL_PATHS:
            raise ValidationFailure(f"Update plan has unsupported eap_kind={plan.eap_kind!r}")
        if payload_sha256(plan.final_payload) != plan.final_payload_sha256:
            raise ValidationFailure("Update plan final payload hash does not match its contents")
        if payload_sha256(plan.base_payload) != plan.base_payload_sha256:
            raise ValidationFailure("Update plan base payload hash does not match its contents")
        _assert_writable_top_level(plan.base_payload, "Update plan base payload")
        _assert_writable_top_level(plan.final_payload, "Update plan final payload")
        _assert_writable_top_level(plan.patch_payload, "Update plan PATCH payload")
        if payload_sha256(plan.patch_payload) != plan.patch_payload_sha256:
            raise ValidationFailure("Update plan PATCH payload hash does not match its contents")
        if any(field not in plan.final_payload for field in plan.patch_payload):
            raise ValidationFailure(
                "Update plan PATCH payload is not a subset of its final payload"
            )
        return plan


def load_update_plan(path: str | Path) -> UpdatePlan:
    source = Path(path).expanduser().resolve()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"Update plan is unreadable: {source}") from exc
    if not isinstance(raw, Mapping):
        raise ConfigurationError(f"Update plan must be a JSON object: {source}")
    return UpdatePlan.from_dict(raw)


class UpdateEngine:
    def __init__(self, client: Any, application_id: int, eap_kind: EapKind) -> None:
        if application_id <= 0:
            raise ConfigurationError("application_id must be a positive integer")
        if eap_kind not in DETAIL_PATHS:
            raise ConfigurationError(f"Unsupported EAP kind: {eap_kind}")
        self.client = client
        self.application_id = application_id
        self.eap_kind: EapKind = eap_kind

    def _detail_path(self) -> str:
        return DETAIL_PATHS[self.eap_kind].format(id=self.application_id)

    def _load_target(self) -> tuple[dict[str, Any], dict[str, Any], int]:
        application = self.client.get(self._detail_path())
        if not isinstance(application, Mapping):
            raise StateError("Target application response was not a JSON object")
        actual_id = application.get("id")
        if actual_id != self.application_id:
            raise StateError(
                "Target application ID mismatch: "
                f"expected {self.application_id}, actual {actual_id!r}"
            )
        registration_id = application.get("eap_registration")
        if isinstance(registration_id, Mapping):
            registration_id = registration_id.get("id", registration_id.get("pk"))
        if not isinstance(registration_id, int) or isinstance(registration_id, bool):
            raise StateError("Target application has no valid registration ID")
        registration = self.client.get(f"/eap-registration/{registration_id}/")
        if not isinstance(registration, Mapping):
            raise StateError("Target registration response was not a JSON object")
        if registration.get("status") != 10:
            raise SafetyError(
                "Updates are limited to registrations in Under Development (status 10): "
                f"status={registration.get('status')!r}"
            )
        if application.get("is_locked") is not False:
            raise SafetyError(
                "Updates are limited to unlocked applications: "
                f"is_locked={application.get('is_locked')!r}"
            )
        modified_at = application.get("modified_at")
        if not isinstance(modified_at, str) or not modified_at:
            raise StateError("Target application has no usable modified_at value")
        return dict(application), dict(registration), registration_id

    def prepare(self, document: UpdateChangeDocument) -> UpdatePlan:
        remote, _, registration_id = self._load_target()
        contract_summary = validate_openapi_contract(
            self.client.get(f"{STAGE_ORIGIN}/api-docs/?format=json"), self.eap_kind
        )
        catalog = fetch_reference_catalog(self.client)
        final_remote = apply_changes(remote, document.changes)
        base_payload = writable_payload(remote)
        final_payload = writable_payload(final_remote)
        patch_payload = {
            field: copy.deepcopy(final_payload[field]) for field in document.changes
        }
        _assert_writable_top_level(final_payload, "Update final payload")
        _assert_writable_top_level(patch_payload, "Update PATCH payload")
        validate_narrative_limits(final_payload, self.eap_kind)
        validate_application_file_payload(final_payload)
        catalog.validate_payload(final_payload)
        modified_at = remote.get("modified_at")
        if not isinstance(modified_at, str) or not modified_at:
            raise StateError("Target application has no usable modified_at value")
        differences = compare_expected_payload(base_payload, final_payload).differences
        return UpdatePlan(
            application_id=self.application_id,
            eap_kind=self.eap_kind,
            registration_id=registration_id,
            base_modified_at=modified_at,
            base_version=remote.get("version") if isinstance(remote.get("version"), int) else None,
            base_payload=base_payload,
            final_payload=final_payload,
            patch_payload=patch_payload,
            changes=copy.deepcopy(document.changes),
            differences=differences,
            base_payload_sha256=payload_sha256(base_payload),
            final_payload_sha256=payload_sha256(final_payload),
            patch_payload_sha256=payload_sha256(patch_payload),
            catalog_summary=catalog.summary(),
            contract_summary=contract_summary,
            created_at=utc_now(),
        )

    def apply(self, plan: UpdatePlan, artifact_root: Path) -> dict[str, Any]:
        if plan.application_id != self.application_id or plan.eap_kind != self.eap_kind:
            raise StateError("Update plan target does not match the selected application")
        _assert_writable_top_level(plan.final_payload, "Update plan final payload")
        _assert_writable_top_level(plan.patch_payload, "Update plan PATCH payload")
        validate_openapi_contract(
            self.client.get(f"{STAGE_ORIGIN}/api-docs/?format=json"), self.eap_kind
        )
        catalog = fetch_reference_catalog(self.client)
        catalog.validate_payload(plan.final_payload)
        validate_narrative_limits(plan.final_payload, self.eap_kind)
        validate_application_file_payload(plan.final_payload)
        remote, _, registration_id = self._load_target()
        if registration_id != plan.registration_id:
            raise StateError(
                "Target registration changed: "
                f"expected {plan.registration_id}, actual {registration_id}"
            )
        if remote.get("modified_at") != plan.base_modified_at:
            raise RecoveryRequired(
                "Target EAP changed after update planning; re-run update-plan before applying"
            )
        if payload_sha256(writable_payload(remote)) != plan.base_payload_sha256:
            raise RecoveryRequired(
                "Target EAP content changed after update planning; "
                "re-run update-plan before applying"
            )
        patch_payload = {
            **copy.deepcopy(plan.patch_payload),
            "modified_at": plan.base_modified_at,
        }
        try:
            self.client.patch_json(self._detail_path(), patch_payload)
            patch_correlation_id = self.client.last_correlation_id
        except RecoveryRequired:
            raise
        updated = self.client.get(self._detail_path())
        if not isinstance(updated, Mapping):
            raise SafetyError("Updated application response was not a JSON object")
        if updated.get("id") != self.application_id:
            raise SafetyError("Updated application ID does not match the requested target")
        if updated.get("eap_registration") != plan.registration_id:
            raise SafetyError("Updated application registration link changed unexpectedly")
        if updated.get("is_locked") is not False:
            raise SafetyError("Updated application is not unlocked")
        if plan.base_version is not None and updated.get("version") != plan.base_version:
            raise SafetyError("Updating a draft unexpectedly changed its version")
        report = compare_expected_payload(plan.final_payload, writable_payload(updated))
        if not report.ok:
            raise SafetyError(f"Updated application verification failed: {report.differences[:10]}")
        receipt = {
            "operation": "update",
            "application_id": self.application_id,
            "eap_kind": self.eap_kind,
            "registration_id": plan.registration_id,
            "completed_at": utc_now(),
            "modified_at_before": plan.base_modified_at,
            "modified_at_after": updated.get("modified_at"),
            "version": updated.get("version"),
            "is_locked": updated.get("is_locked"),
            "verification": report.to_dict(),
            "final_payload_sha256": plan.final_payload_sha256,
            "correlation_id": patch_correlation_id,
        }
        receipt_path = (
            artifact_root
            / f"eap-{self.eap_kind}-{self.application_id}-update-receipt.json"
        )
        atomic_write_json(receipt_path, redact(receipt))
        return {"receipt": str(receipt_path), "verification": report.to_dict()}

    def verify(self, plan: UpdatePlan) -> dict[str, Any]:
        remote, _, registration_id = self._load_target()
        if registration_id != plan.registration_id:
            raise StateError("Update verification target registration does not match the plan")
        report = compare_expected_payload(plan.final_payload, writable_payload(remote))
        if not report.ok:
            raise SafetyError(f"Updated application verification failed: {report.differences[:10]}")
        return {
            "operation": "update_verify",
            "application_id": self.application_id,
            "eap_kind": self.eap_kind,
            "registration_id": registration_id,
            "modified_at": remote.get("modified_at"),
            "version": remote.get("version"),
            "is_locked": remote.get("is_locked"),
            "verification": report.to_dict(),
        }
