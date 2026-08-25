"""Full EAP case and application input models."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from .common import (
    Admin2Selection,
    CaseModel,
    CompletenessProfile,
    EarlyAction,
    EnablingApproach,
    FileReference,
    FileSpec,
    KeyActor,
    PartnerContact,
    PlannedOperation,
    PrioritizedImpact,
    SourceInformation,
    validate_email,
)
from .registration import RegistrationCase


class FullEapApplication(CaseModel):
    partners: list[int] = Field(default_factory=list)
    include_rcrc_climate_center: bool = True
    cover_image_file: FileReference
    total_people_targeted: int = Field(ge=10_000, le=10_000_000)
    national_society_contact_name: str = Field(min_length=1)
    national_society_contact_title: str = Field(min_length=1)
    national_society_contact_email: str
    national_society_contact_phone_number: str = ""
    partner_contacts: list[PartnerContact] = Field(default_factory=list)
    dref_focal_point_name: str | None = None
    dref_focal_point_title: str | None = None
    dref_focal_point_email: str | None = None
    dref_focal_point_phone_number: str = ""
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
    expected_submission_time: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    is_worked_with_government: bool
    worked_with_government_description: str | None = None
    key_actors: list[KeyActor] = Field(min_length=1)
    is_technical_working_groups: bool
    technically_working_group_title: str | None = None
    technical_working_groups_in_place_description: str | None = None
    hazard_selection: str | None = None
    hazard_selection_files: list[FileReference] = Field(default_factory=list, max_length=5)
    exposed_element_and_vulnerability_factor: str | None = None
    exposed_element_and_vulnerability_factor_files: list[FileReference] = Field(
        default_factory=list, max_length=5
    )
    prioritized_impact: str | None = None
    prioritized_impacts: list[PrioritizedImpact] = Field(min_length=1)
    prioritized_impact_files: list[FileReference] = Field(default_factory=list, max_length=5)
    risk_analysis_relevant_files: list[FileReference] = Field(default_factory=list)
    risk_analysis_source_of_information: list[SourceInformation] = Field(default_factory=list)
    trigger_statement: str | None = None
    lead_timeframe_unit: int | None = None
    lead_time: str | None = None
    trigger_statement_source_of_information: list[SourceInformation] = Field(default_factory=list)
    forecast_selection: str | None = None
    forecast_selection_files: list[FileReference] = Field(default_factory=list, max_length=5)
    forecast_table_file: FileReference
    definition_and_justification_impact_level: str | None = None
    definition_and_justification_impact_level_files: list[FileReference] = Field(
        default_factory=list, max_length=5
    )
    identification_of_the_intervention_area: str | None = None
    identification_of_the_intervention_area_files: list[FileReference] = Field(
        default_factory=list, max_length=5
    )
    trigger_model_relevant_files: list[FileReference] = Field(default_factory=list)
    trigger_model_source_of_information: list[SourceInformation] = Field(default_factory=list)
    early_actions: list[EarlyAction] = Field(min_length=1)
    early_action_selection_process: str | None = None
    early_action_selection_process_files: list[FileReference] = Field(
        default_factory=list, max_length=5
    )
    theory_of_change_table_file: FileReference
    evidence_base: str | None = None
    evidence_base_relevant_files: list[FileReference] = Field(default_factory=list)
    evidence_base_source_of_information: list[SourceInformation] = Field(default_factory=list)
    usefulness_of_actions: str | None = None
    feasibility: str | None = None
    early_action_implementation_process: str | None = None
    early_action_implementation_files: list[FileReference] = Field(
        default_factory=list, max_length=5
    )
    trigger_activation_system: str | None = None
    trigger_activation_system_files: list[FileReference] = Field(default_factory=list, max_length=5)
    activation_process_relevant_files: list[FileReference] = Field(default_factory=list)
    activation_process_source_of_information: list[SourceInformation] = Field(default_factory=list)
    selection_of_target_population: str | None = None
    stop_mechanism: str | None = None
    meal: str | None = None
    meal_relevant_files: list[FileReference] = Field(default_factory=list)
    meal_source_of_information: list[SourceInformation] = Field(default_factory=list)
    operational_administrative_capacity: str | None = None
    capacity_relevant_files: list[FileReference] = Field(default_factory=list)
    ns_capacity_source_of_information: list[SourceInformation] = Field(default_factory=list)
    strategies_and_plans: str | None = None
    advance_financial_capacity: str | None = None
    planned_operations: list[PlannedOperation] = Field(default_factory=list)
    enabling_approaches: list[EnablingApproach] = Field(default_factory=list)
    budget_file: FileReference | None = None
    total_budget: int | None = Field(default=None, gt=0)
    readiness_budget: int | None = Field(default=None, gt=0)
    pre_positioning_budget: int | None = Field(default=None, gt=0)
    early_action_budget: int | None = Field(default=None, gt=0)
    budget_description: str | None = None
    readiness_cost_description: str | None = None
    prepositioning_cost_description: str | None = None
    early_action_cost_description: str | None = None
    eap_endorsement: str | None = None

    _validate_ns_email = field_validator("national_society_contact_email")(validate_email)
    _validate_dref_email = field_validator("dref_focal_point_email")(validate_email)
    _validate_regional_focal_email = field_validator("ifrc_regional_focal_point_email")(
        validate_email
    )
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


class FullCase(CaseModel):
    eap_kind: Literal["full"]
    migration_key: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    registration: RegistrationCase
    admin2_selection: Admin2Selection = Field(default_factory=Admin2Selection)
    files: dict[str, FileSpec] = Field(min_length=1)
    application: FullEapApplication
    completeness_profile: CompletenessProfile = "draft"
