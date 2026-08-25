"""Full EAP validation and payload construction."""

from __future__ import annotations

from typing import Any

from ..exceptions import ValidationFailure
from ..models import EapCase, FullCase
from ..verification import VerificationReport, compare_expected_payload
from ..word_limits import validate_narrative_limits
from .base import (
    ResolvedContext,
    enforce_completeness_profile,
    model_payload,
    positive_budget_check,
    require_case_kind,
    require_file_references,
    validate_activity,
)

CAPTIONED_FILE_FIELDS = (
    "hazard_selection_files",
    "exposed_element_and_vulnerability_factor_files",
    "prioritized_impact_files",
    "forecast_selection_files",
    "definition_and_justification_impact_level_files",
    "identification_of_the_intervention_area_files",
    "early_action_selection_process_files",
    "early_action_implementation_files",
    "trigger_activation_system_files",
)
ID_LIST_FILE_FIELDS = (
    "risk_analysis_relevant_files",
    "trigger_model_relevant_files",
    "evidence_base_relevant_files",
    "activation_process_relevant_files",
    "meal_relevant_files",
    "capacity_relevant_files",
)
SINGLE_FILE_FIELDS = ("forecast_table_file", "theory_of_change_table_file", "budget_file")


class FullEapAdapter:
    eap_kind = "full"
    registration_type = 10
    collection_path = "/full-eap/"
    detail_path_template = "/full-eap/{id}/"

    def validate_case(self, case: EapCase) -> None:
        require_case_kind(case, self.eap_kind)
        if not isinstance(case, FullCase):
            raise ValidationFailure("Full adapter received an incompatible case model")
        validate_narrative_limits(case.application.model_dump(mode="python"), self.eap_kind)
        enforce_completeness_profile(case)
        if case.registration.eap_type != self.registration_type:
            raise ValidationFailure("Full cases must use registration eap_type 10")
        app = case.application
        if app.lead_timeframe_unit is not None and app.lead_timeframe_unit not in {10, 20, 30, 40}:
            raise ValidationFailure("Full lead timeframe unit is unsupported")
        if not app.key_actors or not app.early_actions or not app.prioritized_impacts:
            raise ValidationFailure(
                "Full application requires key actors, early actions, and impacts"
            )
        for operation in app.planned_operations:
            for activity in operation.readiness_activities:
                validate_activity(activity, require_timeframe=True)
            for activity in operation.prepositioning_activities:
                validate_activity(activity, require_timeframe=False, require_activation=True)
            for activity in operation.early_action_activities:
                validate_activity(activity, require_timeframe=True)
        for approach in app.enabling_approaches:
            for activity in approach.readiness_activities:
                validate_activity(activity, require_timeframe=True)
            for activity in approach.prepositioning_activities:
                validate_activity(activity, require_timeframe=False, require_activation=True)
            for activity in approach.early_action_activities:
                validate_activity(activity, require_timeframe=True)
        positive_budget_check(case, app)
        refs: list[str] = [app.cover_image_file]
        for field in (*CAPTIONED_FILE_FIELDS, *ID_LIST_FILE_FIELDS, *SINGLE_FILE_FIELDS):
            value = getattr(app, field)
            refs.extend(value if isinstance(value, list) else [value])
        require_file_references(case, refs)

    def build_create_payload(self, case: EapCase, ctx: ResolvedContext) -> dict[str, Any]:
        if not isinstance(case, FullCase):
            raise ValidationFailure("Full adapter received an incompatible case model")
        self.validate_case(case)
        payload = model_payload(case.application)
        payload.update({"eap_registration": ctx.registration_id, "admin2": ctx.admin2_ids})
        if case.application.cover_image_file is not None:
            payload["cover_image_file"] = ctx.file_object(case.application.cover_image_file)
        for field in CAPTIONED_FILE_FIELDS:
            payload[field] = [ctx.file_object(ref) for ref in getattr(case.application, field)]
        for field in ID_LIST_FILE_FIELDS:
            payload[field] = [ctx.file_id(ref) for ref in getattr(case.application, field)]
        for field in SINGLE_FILE_FIELDS:
            value = getattr(case.application, field)
            if value is not None:
                payload[field] = ctx.file_id(value)
        return payload

    def verify(self, expected: dict[str, Any], actual: dict[str, Any]) -> VerificationReport:
        return compare_expected_payload(expected, actual)
