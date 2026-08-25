"""Adapter protocol and shared payload-reference utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..exceptions import StateError, ValidationFailure
from ..models import EapCase
from ..models.common import CaseModel
from ..verification import VerificationReport


@dataclass(frozen=True, slots=True)
class ResolvedContext:
    registration_id: int
    admin2_ids: list[int]
    file_ids: dict[str, int]
    file_captions: dict[str, str]

    def file_id(self, reference: str) -> int:
        try:
            return self.file_ids[reference]
        except KeyError as exc:
            raise StateError(
                f"No uploaded EAP file ID is available for logical file '{reference}'"
            ) from exc

    def file_object(self, reference: str) -> dict[str, Any]:
        return {
            "id": self.file_id(reference),
            "caption": self.file_captions.get(reference, reference),
        }


class EapAdapter(Protocol):
    eap_kind: str
    registration_type: int
    collection_path: str
    detail_path_template: str

    def validate_case(self, case: EapCase) -> None: ...

    def build_create_payload(self, case: EapCase, ctx: ResolvedContext) -> dict[str, Any]: ...

    def verify(self, expected: dict[str, Any], actual: dict[str, Any]) -> VerificationReport: ...


def require_case_kind(case: EapCase, expected: str) -> None:
    if getattr(case, "eap_kind", None) != expected:
        raise ValidationFailure(f"Expected a {expected} case for the selected adapter")


def require_file_references(case: EapCase, references: list[str]) -> None:
    missing = sorted(set(references) - set(case.files))
    if missing:
        raise ValidationFailure(
            "Logical file references are absent from the manifest: " + ", ".join(missing)
        )


def adapter_for_case(case: EapCase) -> EapAdapter:
    if case.eap_kind == "simplified":
        from .simplified import SimplifiedEapAdapter

        return SimplifiedEapAdapter()
    if case.eap_kind == "full":
        from .full import FullEapAdapter

        return FullEapAdapter()
    raise ValidationFailure(f"Unsupported eap_kind: {case.eap_kind}")


def positive_budget_check(case: Any, application: Any) -> None:
    budget_values = (
        application.total_budget,
        application.readiness_budget,
        application.pre_positioning_budget,
        application.early_action_budget,
    )
    if any(value is None for value in budget_values):
        return
    if not application.planned_operations or not application.enabling_approaches:
        return
    component_total = (
        application.readiness_budget
        + application.pre_positioning_budget
        + application.early_action_budget
    )
    if component_total != application.total_budget:
        raise ValidationFailure(
            f"{case.eap_kind} component budgets total {component_total}, "
            f"but total_budget is {application.total_budget}"
        )
    operation_total = sum(row.budget_per_sector for row in application.planned_operations)
    approach_total = sum(row.budget_per_approach for row in application.enabling_approaches)
    if operation_total + approach_total != application.total_budget:
        raise ValidationFailure(
            f"{case.eap_kind} operation and approach budgets total "
            f"{operation_total + approach_total}, but total_budget is {application.total_budget}"
        )


def completeness_warnings(case: Any) -> list[str]:
    """Report submission/readiness gaps without blocking a draft migration."""

    application = case.application
    warnings: list[str] = []
    for field_name, value in application.model_dump(mode="python").items():
        if field_name in {
            "include_rcrc_climate_center",
            "partners",
            "cover_image_file",
            "budget_file",
        } or field_name.endswith("_files"):
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            warnings.append(f"application.{field_name} is not completed")
        elif isinstance(value, list) and not value:
            warnings.append(f"application.{field_name} is empty")
    return warnings


def enforce_completeness_profile(case: Any) -> None:
    if getattr(case, "completeness_profile", "draft") != "strict":
        return
    warnings = completeness_warnings(case)
    if warnings:
        raise ValidationFailure(
            "Strict completeness profile found unfinished fields: "
            + "; ".join(warnings[:10])
        )


def validate_activity(
    activity: Any, *, require_timeframe: bool, require_activation: bool = False
) -> None:
    if require_timeframe and activity.timeframe is None:
        raise ValidationFailure(f"Activity '{activity.activity}' must include timeframe")
    if activity.timeframe is not None and activity.timeframe not in {10, 20, 30, 40}:
        raise ValidationFailure(f"Activity '{activity.activity}' has an unsupported timeframe")
    if require_timeframe and not activity.time_value:
        raise ValidationFailure(
            f"Activity '{activity.activity}' must include a non-empty time_value"
        )
    if activity.time_value and any(value <= 0 for value in activity.time_value):
        raise ValidationFailure(f"Activity '{activity.activity}' has a non-positive time_value")
    if require_activation and (activity.activation_one is None or activity.activation_two is None):
        raise ValidationFailure(
            f"Pre-positioning activity '{activity.activity}' must include activation_one "
            "and activation_two"
        )


def model_payload(model: CaseModel) -> dict[str, Any]:
    # Nullable draft fields must remain explicit nulls rather than silently
    # disappearing during payload construction.
    return model.model_dump(mode="json", exclude_none=False)
