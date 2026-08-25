"""Simplified EAP case and application input models."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from .common import (
    Admin2Selection,
    CaseModel,
    CompletenessProfile,
    ContactFields,
    EnablingApproach,
    FileReference,
    FileSpec,
    PartnerContact,
    PlannedOperation,
    validate_email,
)
from .registration import RegistrationCase


class SimplifiedEapApplication(ContactFields):
    partners: list[int] = Field(default_factory=list)
    include_rcrc_climate_center: bool = True
    cover_image_file: FileReference | None = None
    seap_timeframe: int
    ifrc_regional_focal_point_name: str | None = None
    ifrc_regional_focal_point_title: str | None = None
    ifrc_regional_focal_point_email: str | None = None
    ifrc_regional_focal_point_phone_number: str = ""
    ifrc_regional_ops_manager_name: str | None = None
    ifrc_regional_ops_manager_title: str | None = None
    ifrc_regional_ops_manager_email: str | None = None
    ifrc_regional_ops_manager_phone_number: str = ""
    ifrc_regional_head_dcc_name: str | None = None
    ifrc_regional_head_dcc_title: str | None = None
    ifrc_regional_head_dcc_email: str | None = None
    ifrc_regional_head_dcc_phone_number: str = ""
    partner_contacts: list[PartnerContact] = Field(default_factory=list)
    prioritized_hazard_and_impact: str | None = None
    hazard_impact_files: list[FileReference] = Field(default_factory=list, max_length=5)
    risks_selected_protocols: str | None = None
    risk_selected_protocols_files: list[FileReference] = Field(default_factory=list, max_length=5)
    selected_early_actions: str | None = None
    selected_early_actions_files: list[FileReference] = Field(default_factory=list, max_length=5)
    overall_objective_intervention: str | None = None
    potential_geographical_high_risk_areas: str | None = None
    total_people_targeted: int | None = Field(default=None, ge=2000, le=10_000_000)
    assisted_through_operation: str | None = None
    selection_criteria: str | None = None
    trigger_statement: str | None = None
    seap_lead_timeframe_unit: int | None = None
    seap_lead_time: int | None = Field(default=None, gt=0)
    activation_timeframe_unit: int | None = None
    activation_timeframe: int | None = Field(default=None, gt=0)
    trigger_threshold_justification: str | None = None
    next_step_towards_full_eap: str | None = None
    planned_operations: list[PlannedOperation] = Field(default_factory=list)
    enabling_approaches: list[EnablingApproach] = Field(default_factory=list)
    early_action_capability: str | None = None
    rcrc_movement_involvement: str | None = None
    budget_file: FileReference | None = None
    total_budget: int | None = Field(default=None, gt=0)
    readiness_budget: int | None = Field(default=None, gt=0)
    pre_positioning_budget: int | None = Field(default=None, gt=0)
    early_action_budget: int | None = Field(default=None, gt=0)

    _validate_regional_ns_email = field_validator("ifrc_regional_focal_point_email")(validate_email)
    _validate_regional_ops_email = field_validator("ifrc_regional_ops_manager_email")(
        validate_email
    )
    _validate_regional_dcc_email = field_validator("ifrc_regional_head_dcc_email")(validate_email)

    @field_validator("partners")
    @classmethod
    def positive_partners(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("partner IDs must be positive integers")
        return values


class SimplifiedCase(CaseModel):
    eap_kind: Literal["simplified"]
    migration_key: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    registration: RegistrationCase
    admin2_selection: Admin2Selection = Field(default_factory=Admin2Selection)
    files: dict[str, FileSpec] = Field(min_length=1)
    application: SimplifiedEapApplication
    completeness_profile: CompletenessProfile = "draft"
