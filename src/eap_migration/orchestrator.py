"""Shared validate, plan, apply, and verify workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .adapters import EapAdapter, ResolvedContext, adapter_for_case
from .adapters.base import completeness_warnings
from .admin2 import Admin2Resolver
from .case_loader import canonical_case_bytes, legacy_canonical_case_bytes
from .catalogs import ReferenceCatalog, fetch_reference_catalog
from .client import EapApiClient
from .contracts import validate_openapi_contract
from .exceptions import ApiError, EapMigrationError, RecoveryRequired, SafetyError, StateError
from .files import (
    DEFAULT_MAX_FILE_SIZE,
    LocalFile,
    all_file_references,
    file_reference_fields,
    validate_files,
)
from .logging import RedactingLoggerAdapter
from .models import EapCase
from .paths import (
    default_artifact_root,
    default_state_root,
    find_repository_root,
    legacy_state_path,
)
from .references import (
    registration_signature_matches,
    response_id,
    validate_registration_references,
)
from .settings import STAGE_ORIGIN, enforce_stage_origin
from .state import (
    FileState,
    StateRecord,
    StateStore,
    atomic_write_json,
    payload_sha256,
    sha256_bytes,
    utc_now,
)
from .verification import compare_expected_payload

REGISTRATION_RECOVERY_WINDOW = timedelta(hours=24)


@dataclass(slots=True)
class PlanResult:
    case: EapCase
    adapter: EapAdapter
    case_sha256: str
    files: dict[str, LocalFile]
    admin2_ids: list[int]
    admin2_meta: dict[str, Any]
    state: StateRecord | None
    registration_id: int | None
    application_id: int | None
    existing_registration: dict[str, Any] | None
    existing_application: dict[str, Any] | None
    recovered_registration: dict[str, Any] | None
    catalog_summary: dict[str, Any]
    contract_summary: dict[str, Any]
    completeness_warnings: list[str]
    conflicts: list[str]

    def to_dict(self) -> dict[str, Any]:
        state_record = self.state
        state = state_record.model_dump(mode="json") if state_record else None
        return {
            "migration_key": self.case.migration_key,
            "eap_kind": self.case.eap_kind,
            "case_sha256": self.case_sha256,
            "authentication_checked": True,
            "admin2": {**self.admin2_meta, "resolved_ids": self.admin2_ids},
            "reference_catalog": self.catalog_summary,
            "openapi_contract": self.contract_summary,
            "completeness": {
                "profile": self.case.completeness_profile,
                "warnings": self.completeness_warnings,
            },
            "files": {
                key: {
                    "path": str(info.path),
                    "caption": info.caption,
                    "size": info.size,
                    "sha256": info.sha256,
                    "remote_id": state_record.files[key].remote_id
                    if state_record and key in state_record.files
                    else None,
                }
                for key, info in self.files.items()
            },
            "registration": {
                "id": self.registration_id,
                "existing": self.existing_registration is not None,
                "recovered": self.recovered_registration is not None,
                "remote_sha256": payload_sha256(self.existing_registration)
                if self.existing_registration is not None
                else None,
                "payload": self.case.registration.to_payload(),
            },
            "application": {
                "id": self.application_id,
                "existing": self.existing_application is not None,
                "remote_sha256": payload_sha256(self.existing_application)
                if self.existing_application is not None
                else None,
                "collection_path": self.adapter.collection_path,
                "input": self.case.application.model_dump(mode="json"),
            },
            "expected_writes": {
                "file_uploads": [
                    key
                    for key in self.files
                    if not (
                        state_record
                        and key in state_record.files
                        and state_record.files[key].remote_id
                    )
                ],
                "registration_post": self.registration_id is None,
                "application_post": self.application_id is None,
            },
            "conflicts": self.conflicts,
            "state": state,
        }


class MigrationEngine:
    def __init__(
        self,
        case: EapCase,
        case_path: Path,
        client: EapApiClient,
        *,
        state_root: Path | None = None,
        artifact_root: Path | None = None,
        max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE,
        logger: RedactingLoggerAdapter | None = None,
    ) -> None:
        self.case = case
        self.case_path = case_path
        self.client = client
        self.adapter = adapter_for_case(case)
        self.repository_root = find_repository_root(case_path)
        self._state_root_explicit = state_root is not None
        self.state_root = (
            Path(state_root).expanduser().resolve()
            if state_root is not None
            else default_state_root(self.repository_root)
        )
        self.state_store = StateStore(self.state_root)
        self.artifact_root = (
            Path(artifact_root).expanduser().resolve()
            if artifact_root is not None
            else default_artifact_root(self.repository_root)
        )
        self.max_file_size_bytes = max_file_size_bytes
        self.legacy_state_path = legacy_state_path(case_path, case.migration_key)
        self.logger = logger

    def _event(self, level: str, event: str, **data: Any) -> None:
        if self.logger is None:
            return
        log_method = getattr(self.logger, level)
        log_method(event, extra={"event_data": {"migration_key": self.case.migration_key, **data}})

    def _case_sha256(self) -> str:
        return sha256_bytes(canonical_case_bytes(self.case))

    def _legacy_case_sha256(self) -> str:
        return sha256_bytes(legacy_canonical_case_bytes(self.case))

    def _local_files(self) -> dict[str, LocalFile]:
        self.adapter.validate_case(self.case)
        return validate_files(
            self.case_path,
            self.case.files,
            all_file_references(self.case.application),
            max_size=self.max_file_size_bytes,
            field_names=file_reference_fields(self.case.application),
        )

    def _load_state(self, case_sha256: str) -> StateRecord | None:
        state = self.state_store.load(self.case.migration_key)
        if (
            state is None
            and not self._state_root_explicit
            and self.legacy_state_path != self.state_store.path_for(
            self.case.migration_key
            )
            and self.legacy_state_path.exists()
        ):
            raise StateError(
                f"Legacy case-local state exists at {self.legacy_state_path}. "
                "It was not moved or merged. Inspect it with --state-root pointing "
                "to that directory, then migrate it explicitly or review and reset it."
            )
        if state and state.case_sha256 not in {case_sha256, self._legacy_case_sha256()}:
            raise StateError(
                "Case content changed since the saved state was created; reset state explicitly "
                "only after reviewing the remote record"
            )
        return state

    def _find_existing_registration(
        self, expected: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        rows, _ = self.client.get_paginated(
            "/eap-registration/",
            params={
                "country": expected["country"],
                "national_society": expected["national_society"],
                "disaster_type": expected["disaster_type"],
                "eap_type": expected["eap_type"],
                "limit": 100,
            },
        )
        matches = [row for row in rows if registration_signature_matches(row, expected)]
        if len(matches) > 1:
            return None, [
                f"{len(matches)} matching registration candidates were found; "
                "no automatic adoption is safe"
            ]
        return (matches[0] if matches else None), []

    def _find_existing_application(
        self, registration_id: int
    ) -> tuple[dict[str, Any] | None, list[str]]:
        rows, _ = self.client.get_paginated(
            self.adapter.collection_path,
            params={"eap_registration": registration_id, "limit": 100},
        )
        if len(rows) > 1:
            return None, [
                f"{len(rows)} {self.case.eap_kind} application candidates were found "
                "for registration "
                f"{registration_id}"
            ]
        return (rows[0] if rows else None), []

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    def _recover_registration(
        self, state: StateRecord, expected: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        if not state.registration_recovery_required:
            return None, []
        if not state.registration_intent_sha256 or not state.registration_intent_created_at:
            return None, [
                "Registration recovery was requested but the saved pre-POST intent is incomplete"
            ]
        expected_hash = payload_sha256(expected)
        if state.registration_intent_sha256 != expected_hash:
            return None, [
                "Registration recovery stopped because the saved request hash no longer "
                "matches the current case"
            ]
        intent_time = self._parse_timestamp(state.registration_intent_created_at)
        if intent_time is None:
            return None, [
                "Registration recovery stopped because the saved intent timestamp is invalid"
            ]

        candidate, conflicts = self._find_existing_registration(expected)
        if conflicts:
            return None, conflicts
        if candidate is None:
            return None, [
                "Registration recovery found no matching candidate; operator review is required"
            ]
        created_at = self._parse_timestamp(candidate.get("created_at"))
        now = datetime.now(UTC)
        if (
            created_at is None
            or created_at < intent_time - timedelta(minutes=5)
            or created_at > intent_time + REGISTRATION_RECOVERY_WINDOW
            or created_at > now + timedelta(minutes=5)
        ):
            return None, [
                "Registration recovery found a matching record outside the safe recent-time "
                "window; operator review is required"
            ]
        if candidate.get("eap_type") != expected.get("eap_type"):
            return None, [
                "Registration recovery candidate has a different EAP type; operator review "
                "is required"
            ]
        if candidate.get("status") != 10:
            return None, [
                "Registration recovery candidate is not Under Development (status 10)"
            ]
        candidate_id = candidate.get("id", candidate.get("pk"))
        if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id <= 0:
            return None, [
                "Registration recovery candidate has no valid ID; operator review is required"
            ]
        existing_application, application_conflicts = self._find_existing_application(candidate_id)
        if application_conflicts:
            return None, application_conflicts
        if existing_application is not None:
            return None, [
                "Registration recovery candidate already has an application; automatic "
                "adoption is unsafe"
            ]
        return candidate, []

    def validate_local(self) -> dict[str, Any]:
        files = self._local_files()
        snapshot_path = (
            self.repository_root
            / "schemas"
            / "staging-reference-catalog.2026-08-25.json"
        )
        catalog_report: dict[str, Any]
        if snapshot_path.exists():
            catalog = ReferenceCatalog.from_snapshot(snapshot_path)
            catalog.validate_case(self.case)
            catalog_report = {**catalog.summary(), "validation": "passed"}
        else:
            catalog_report = {
                "source": "none",
                "validation": "not_checked",
                "warning": f"No dated reference catalog snapshot found at {snapshot_path}",
            }
        return {
            "migration_key": self.case.migration_key,
            "eap_kind": self.case.eap_kind,
            "case_sha256": self._case_sha256(),
            "repository_root": str(self.repository_root),
            "state_root": str(self.state_root),
            "artifact_root": str(self.artifact_root),
            "token_required_for_network_commands": True,
            "reference_catalog": catalog_report,
            "completeness": {
                "profile": self.case.completeness_profile,
                "warnings": completeness_warnings(self.case),
            },
            "files": {
                key: {
                    "path": str(info.path),
                    "size": info.size,
                    "sha256": info.sha256,
                    "caption": info.caption,
                }
                for key, info in files.items()
            },
            "registration_type": self.adapter.registration_type,
            "validation": "passed",
        }

    def plan(self) -> PlanResult:
        self._event("info", "plan_started", eap_kind=self.case.eap_kind)
        try:
            result = self._plan_impl()
        except EapMigrationError as exc:
            self._event(
                "error",
                "plan_failed",
                error_type=type(exc).__name__,
                message=str(exc),
            )
            raise
        self._event(
            "info",
            "plan_completed",
            conflicts=len(result.conflicts),
            registration_id=result.registration_id,
            application_id=result.application_id,
        )
        return result

    def _plan_impl(self) -> PlanResult:
        files = self._local_files()
        case_sha256 = self._case_sha256()
        state = self._load_state(case_sha256)
        self.client.check_authentication()
        contract_summary = validate_openapi_contract(
            self.client.get(f"{STAGE_ORIGIN}/api-docs/?format=json"), self.case.eap_kind
        )
        catalog = fetch_reference_catalog(self.client)
        catalog.validate_case(self.case)
        validate_registration_references(
            self.client, self.case.registration, self.case.application
        )
        readiness_warnings = completeness_warnings(self.case)

        admin2_ids, admin2_meta = Admin2Resolver(self.client).resolve(self.case.admin2_selection)
        conflicts: list[str] = []
        existing_registration: dict[str, Any] | None = None
        existing_application: dict[str, Any] | None = None
        recovered_registration: dict[str, Any] | None = None
        registration_id = state.registration_id if state else None
        application_id = state.application_id if state else None

        if registration_id:
            try:
                existing_registration = self.client.get(f"/eap-registration/{registration_id}/")
            except ApiError as exc:
                if exc.status_code == 404:
                    raise StateError(
                        f"Saved registration {registration_id} no longer exists; "
                        "reset state explicitly"
                    ) from exc
                raise
        elif state and state.registration_recovery_required:
            recovered_registration, recovery_conflicts = self._recover_registration(
                state, self.case.registration.to_payload()
            )
            conflicts.extend(recovery_conflicts)
            if recovered_registration:
                existing_registration = recovered_registration
                recovered_id = recovered_registration.get(
                    "id", recovered_registration.get("pk")
                )
                registration_id = recovered_id if isinstance(recovered_id, int) else None
        else:
            existing_registration, registration_conflicts = self._find_existing_registration(
                self.case.registration.to_payload()
            )
            conflicts.extend(registration_conflicts)
            if existing_registration:
                value = existing_registration.get("id", existing_registration.get("pk"))
                registration_id = value if isinstance(value, int) else None
                if state is None or state.registration_id is None:
                    conflicts.append(
                        "An existing registration was found without a matching pending "
                        "recovery intent; automatic adoption is disabled"
                    )

        if registration_id:
            if application_id:
                try:
                    existing_application = self.client.get(
                        self.adapter.detail_path_template.format(id=application_id)
                    )
                except ApiError as exc:
                    if exc.status_code == 404:
                        raise StateError(
                            f"Saved application {application_id} no longer exists; "
                            "reset state explicitly"
                        ) from exc
                    raise
            else:
                existing_application, application_conflicts = self._find_existing_application(
                    registration_id
                )
                conflicts.extend(application_conflicts)
                if existing_application:
                    value = existing_application.get("id", existing_application.get("pk"))
                    application_id = value if isinstance(value, int) else None
                    if state is None or state.application_id is None:
                        conflicts.append(
                            "An existing application was found without matching migration "
                            "state; automatic adoption is disabled"
                        )

        result = PlanResult(
            case=self.case,
            adapter=self.adapter,
            case_sha256=case_sha256,
            files=files,
            admin2_ids=admin2_ids,
            admin2_meta=admin2_meta,
            state=state,
            registration_id=registration_id,
            application_id=application_id,
            existing_registration=existing_registration,
            existing_application=existing_application,
            recovered_registration=recovered_registration,
            catalog_summary=catalog.summary(),
            contract_summary=contract_summary,
            completeness_warnings=readiness_warnings,
            conflicts=conflicts,
        )
        return result

    def _new_state(self, plan: PlanResult) -> StateRecord:
        state = plan.state or StateRecord(
            migration_key=self.case.migration_key,
            case_sha256=plan.case_sha256,
        )
        state.admin2_ids = plan.admin2_ids
        if plan.recovered_registration is not None:
            recovered_id = plan.recovered_registration.get(
                "id", plan.recovered_registration.get("pk")
            )
            if isinstance(recovered_id, int) and recovered_id > 0:
                state.registration_id = recovered_id
                state.registration_request_sha256 = state.registration_intent_sha256
                state.registration_intent_sha256 = None
                state.registration_intent_created_at = None
                state.registration_recovery_required = False
        for key, local in plan.files.items():
            existing = state.files.get(key)
            if existing and existing.sha256 != local.sha256:
                raise StateError(
                    f"Local file '{key}' changed after upload; "
                    "reset state only after reviewing staging"
                )
            if not existing:
                state.files[key] = FileState(sha256=local.sha256)
        return state

    def _context(self, state: StateRecord) -> ResolvedContext:
        file_ids = {
            key: item.remote_id for key, item in state.files.items() if item.remote_id is not None
        }
        missing = sorted(set(self.case.files) - set(file_ids))
        if missing:
            # Only referenced files are required; optional unreferenced entries need no upload.
            refs = set(all_file_references(self.case.application))
            missing = sorted(refs - set(file_ids))
        if missing:
            raise StateError("Uploaded file IDs are missing for: " + ", ".join(missing))
        captions = {key: spec.caption for key, spec in self.case.files.items()}
        if state.registration_id is None:
            raise StateError("Registration ID is missing from state")
        return ResolvedContext(state.registration_id, state.admin2_ids, file_ids, captions)

    @staticmethod
    def _remote_file_basename(remote: dict[str, Any]) -> str | None:
        value = remote.get("filename", remote.get("file"))
        if isinstance(value, dict):
            value = value.get("name", value.get("url"))
        if not isinstance(value, str) or not value:
            return None
        parsed = urlparse(value)
        candidate = parsed.path.rsplit("/", 1)[-1]
        return unquote(candidate) if candidate else None

    def _verify_known_file(
        self, remote_id: int, logical_key: str, expected: LocalFile
    ) -> dict[str, Any]:
        remote = self.client.get(f"/eap-file/{remote_id}/")
        if not isinstance(remote, dict):
            raise StateError(f"Remote file '{logical_key}' returned an unexpected response")
        basename = self._remote_file_basename(remote)
        if basename != expected.path.name:
            raise StateError(
                f"Remote file '{logical_key}' does not match the expected basename: "
                f"expected {expected.path.name!r}, actual {basename!r}"
            )
        actual_caption = remote.get("caption")
        if actual_caption != expected.caption:
            raise StateError(
                f"Remote file '{logical_key}' does not match the expected caption: "
                f"expected {expected.caption!r}, actual {actual_caption!r}"
            )
        return {
            "remote_basename": basename,
            "remote_caption": actual_caption,
            "remote_response_at": utc_now(),
        }

    def _verify_registration_status(self, registration: dict[str, Any]) -> None:
        if registration.get("status") != 10:
            raise SafetyError(
                "Registration status is not Under Development (numeric status 10): "
                f"status={registration.get('status')!r}, "
                f"display={registration.get('status_display')!r}"
            )

    def _verify_registration_invariants(
        self, registration: dict[str, Any]
    ) -> Any:
        self._verify_registration_status(registration)
        report = compare_expected_payload(self.case.registration.to_payload(), registration)
        if not report.ok:
            raise SafetyError(
                f"Registration verification failed: {report.differences[:10]}"
            )
        return report

    @staticmethod
    def _application_registration_id(application: dict[str, Any]) -> int | None:
        value = application.get("eap_registration")
        if isinstance(value, dict):
            value = value.get("id", value.get("pk"))
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _verify_application_invariants(
        self, application: dict[str, Any], expected_registration_id: int
    ) -> None:
        actual_registration_id = self._application_registration_id(application)
        if actual_registration_id != expected_registration_id:
            raise SafetyError(
                "Application belongs to the wrong registration: "
                f"expected {expected_registration_id}, actual {actual_registration_id!r}"
            )
        if application.get("version") != 1:
            raise SafetyError(
                f"New migration application must have version 1, got {application.get('version')!r}"
            )
        if application.get("is_locked") is not False:
            raise SafetyError(
                "New migration application must be unlocked (is_locked=false), "
                f"got {application.get('is_locked')!r}"
            )

    def apply(self, *, confirm_stage_writes: bool) -> dict[str, Any]:
        self._event("info", "apply_started", eap_kind=self.case.eap_kind)
        try:
            result = self._apply_impl(confirm_stage_writes=confirm_stage_writes)
        except EapMigrationError as exc:
            self._event(
                "error",
                "apply_failed",
                error_type=type(exc).__name__,
                message=str(exc),
            )
            raise
        self._event("info", "apply_completed")
        return result

    def _apply_impl(self, *, confirm_stage_writes: bool) -> dict[str, Any]:
        enforce_stage_origin(STAGE_ORIGIN)
        if not confirm_stage_writes:
            raise SafetyError("apply requires --confirm-stage-writes")

        with self.state_store.lock_for(self.case.migration_key):
            plan = self.plan()
            if plan.conflicts:
                raise StateError("; ".join(plan.conflicts))
            if plan.existing_registration and (
                not plan.state or plan.state.registration_id is None
            ) and plan.recovered_registration is None:
                raise StateError(
                    "An existing registration was found without matching migration state "
                    "or pending recovery intent; automatic adoption is disabled"
                )
            if plan.existing_application and (not plan.state or plan.state.application_id is None):
                raise StateError(
                    "An existing application was found without migration state; "
                    "automatic adoption is disabled"
                )

            state = self._new_state(plan)
            self.state_store.save(state)
            correlation_ids: dict[str, str | None] = {}

            for key, local in plan.files.items():
                item = state.files[key]
                if item.remote_id is not None:
                    metadata = self._verify_known_file(item.remote_id, key, local)
                    correlation_ids[f"file:{key}:verify"] = self.client.last_correlation_id
                    item.remote_basename = metadata["remote_basename"]
                    item.remote_caption = metadata["remote_caption"]
                    item.remote_response_at = metadata["remote_response_at"]
                    self.state_store.save(state)
                    continue
                response = self.client.upload_file(local.path, caption=local.caption)
                correlation_ids[f"file:{key}:upload"] = self.client.last_correlation_id
                remote_id = response_id(response, f"file '{key}'")
                item.remote_id = remote_id
                self.state_store.save(state)
                metadata = self._verify_known_file(remote_id, key, local)
                correlation_ids[f"file:{key}:verify"] = self.client.last_correlation_id
                item.remote_basename = metadata["remote_basename"]
                item.remote_caption = metadata["remote_caption"]
                item.remote_response_at = metadata["remote_response_at"]
                self.state_store.save(state)

            if state.registration_id is None:
                registration_payload = self.case.registration.to_payload()
                request_hash = payload_sha256(registration_payload)
                state.registration_intent_sha256 = request_hash
                state.registration_intent_created_at = utc_now()
                state.registration_recovery_required = True
                self.state_store.save(state)
                try:
                    response = self.client.post_json("/eap-registration/", registration_payload)
                    correlation_ids["registration:post"] = self.client.last_correlation_id
                    state.registration_id = response_id(response, "registration")
                except RecoveryRequired:
                    raise
                except (ApiError, StateError) as exc:
                    self.state_store.save(state)
                    raise RecoveryRequired(
                        "Registration creation returned an unusable result; inspect staging "
                        "before retrying"
                    ) from exc
                state.registration_request_sha256 = request_hash
                state.registration_intent_sha256 = None
                state.registration_intent_created_at = None
                state.registration_recovery_required = False
                self.state_store.save(state)
            registration = self.client.get(f"/eap-registration/{state.registration_id}/")
            correlation_ids["registration:get"] = self.client.last_correlation_id
            registration_report = self._verify_registration_invariants(registration)

            context = self._context(state)
            application_payload = self.adapter.build_create_payload(self.case, context)
            if state.application_id is None:
                response = self.client.post_json(self.adapter.collection_path, application_payload)
                correlation_ids["application:post"] = self.client.last_correlation_id
                state.application_id = response_id(response, f"{self.case.eap_kind} application")
                state.application_kind = self.case.eap_kind
                state.application_request_sha256 = payload_sha256(application_payload)
                self.state_store.save(state)
            application = self.client.get(
                self.adapter.detail_path_template.format(id=state.application_id)
            )
            correlation_ids["application:get"] = self.client.last_correlation_id
            self._verify_application_invariants(application, state.registration_id)
            report = self.adapter.verify(application_payload, application)
            if not report.ok:
                raise SafetyError(
                    f"Application verification failed after apply: {report.differences[:10]}"
                )
            registration = self.client.get(f"/eap-registration/{state.registration_id}/")
            correlation_ids["registration:final_get"] = self.client.last_correlation_id
            registration_report = self._verify_registration_invariants(registration)

            state.last_verified_at = utc_now()
            self.state_store.save(state)
            receipt = {
                "migration_key": self.case.migration_key,
                "completed_at": state.last_verified_at,
                "registration_id": state.registration_id,
                "application_kind": state.application_kind,
                "application_id": state.application_id,
                "files": {
                    key: {"remote_id": item.remote_id, "sha256": item.sha256}
                    for key, item in state.files.items()
                },
                "verification": {
                    "application": report.to_dict(),
                    "registration": registration_report.to_dict(),
                    "registration_status": registration.get("status"),
                    "registration_status_display": registration.get("status_display"),
                    "application_version": application.get("version"),
                    "application_is_locked": application.get("is_locked"),
                    "application_registration_id": self._application_registration_id(
                        application
                    ),
                    "correlation_ids": correlation_ids,
                },
            }
            receipt_path = self.artifact_root / f"{self.case.migration_key}-receipt.json"
            atomic_write_json(receipt_path, receipt)
            return {"state": state.model_dump(mode="json"), "receipt": str(receipt_path)}

    def verify(self) -> dict[str, Any]:
        plan = self.plan()
        if not plan.state or not plan.state.registration_id or not plan.state.application_id:
            raise StateError("No complete state exists; apply the case before running verify")
        context = self._context(plan.state)
        expected_application = self.adapter.build_create_payload(self.case, context)
        actual_application = self.client.get(
            self.adapter.detail_path_template.format(id=plan.state.application_id)
        )
        self._verify_application_invariants(actual_application, plan.state.registration_id)
        application_report = self.adapter.verify(expected_application, actual_application)
        actual_registration = self.client.get(f"/eap-registration/{plan.state.registration_id}/")
        self._verify_registration_invariants(actual_registration)
        registration_report = compare_expected_payload(
            self.case.registration.to_payload(), actual_registration
        )
        if not application_report.ok or not registration_report.ok:
            raise SafetyError(
                f"Verification failed: application={application_report.differences[:10]}, "
                f"registration={registration_report.differences[:10]}"
            )
        plan.state.last_verified_at = utc_now()
        self.state_store.save(plan.state)
        return {
            "migration_key": self.case.migration_key,
            "registration_id": plan.state.registration_id,
            "application_id": plan.state.application_id,
            "application": application_report.to_dict(),
            "registration": registration_report.to_dict(),
            "status": "Under Development",
        }
