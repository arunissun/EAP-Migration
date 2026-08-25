"""Simplified EAP validation and payload construction."""

from __future__ import annotations

from typing import Any

from ..exceptions import ValidationFailure
from ..models import EapCase, SimplifiedCase
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


class SimplifiedEapAdapter:
    eap_kind = "simplified"
    registration_type = 20
    collection_path = "/simplified-eap/"
    detail_path_template = "/simplified-eap/{id}/"

    def validate_case(self, case: EapCase) -> None:
        require_case_kind(case, self.eap_kind)
        if not isinstance(case, SimplifiedCase):
            raise ValidationFailure("Simplified adapter received an incompatible case model")
        validate_narrative_limits(case.application.model_dump(mode="python"), self.eap_kind)
        enforce_completeness_profile(case)
        if case.registration.eap_type != self.registration_type:
            raise ValidationFailure("Simplified cases must use registration eap_type 20")
        app = case.application
        if app.seap_lead_timeframe_unit is not None and app.seap_lead_timeframe_unit not in {
            20,
            30,
            40,
        }:
            raise ValidationFailure("Simplified lead timeframe unit must be Months, Days, or Hours")
        if app.activation_timeframe_unit is not None and app.activation_timeframe_unit != 20:
            raise ValidationFailure("Simplified activation timeframe unit must be Months (20)")
        if case.completeness_profile == "strict" and not app.partner_contacts:
            raise ValidationFailure("Simplified application requires at least one partner contact")
        for operation in app.planned_operations:
            for activity in (
                *operation.readiness_activities,
                *operation.prepositioning_activities,
                *operation.early_action_activities,
            ):
                validate_activity(activity, require_timeframe=True)
        for approach in app.enabling_approaches:
            for activity in (
                *approach.readiness_activities,
                *approach.prepositioning_activities,
                *approach.early_action_activities,
            ):
                validate_activity(activity, require_timeframe=True)
        positive_budget_check(case, app)
        refs = []
        for field in (
            "cover_image_file",
            "hazard_impact_files",
            "risk_selected_protocols_files",
            "selected_early_actions_files",
            "budget_file",
        ):
            value = getattr(app, field)
            refs.extend(value if isinstance(value, list) else [value])
        require_file_references(case, refs)

    def build_create_payload(self, case: EapCase, ctx: ResolvedContext) -> dict[str, Any]:
        if not isinstance(case, SimplifiedCase):
            raise ValidationFailure("Simplified adapter received an incompatible case model")
        self.validate_case(case)
        payload = model_payload(case.application)
        payload.update({"eap_registration": ctx.registration_id, "admin2": ctx.admin2_ids})
        if case.application.cover_image_file is not None:
            payload["cover_image_file"] = ctx.file_object(case.application.cover_image_file)
        payload["hazard_impact_files"] = [
            ctx.file_object(ref) for ref in case.application.hazard_impact_files
        ]
        payload["risk_selected_protocols_files"] = [
            ctx.file_object(ref) for ref in case.application.risk_selected_protocols_files
        ]
        payload["selected_early_actions_files"] = [
            ctx.file_object(ref) for ref in case.application.selected_early_actions_files
        ]
        if case.application.budget_file is not None:
            payload["budget_file"] = ctx.file_id(case.application.budget_file)
        return payload

    def verify(self, expected: dict[str, Any], actual: dict[str, Any]) -> VerificationReport:
        return compare_expected_payload(expected, actual)
