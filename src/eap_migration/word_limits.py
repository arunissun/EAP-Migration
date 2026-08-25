"""Frontend-aligned narrative word limits; over-limit text is never truncated."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .exceptions import ValidationFailure

WORD_PATTERN = re.compile(r"\S+")

SIMPLIFIED_WORD_LIMITS = {
    "prioritized_hazard_and_impact": 500,
    "risks_selected_protocols": 150,
    "selected_early_actions": 150,
    "overall_objective_intervention": 150,
    "potential_geographical_high_risk_areas": 100,
    "assisted_through_operation": 100,
    "selection_criteria": 100,
    "trigger_statement": 100,
    "trigger_threshold_justification": 100,
    "next_step_towards_full_eap": 100,
    "early_action_capability": 500,
    "rcrc_movement_involvement": 150,
}

FULL_WORD_LIMITS = {
    "key_actors": 150,
    "technical_working_groups_in_place_description": 150,
    "hazard_selection": 1000,
    "exposed_element_and_vulnerability_factor": 1500,
    "prioritized_impact": 500,
    "trigger_statement": 200,
    "forecast_selection": 750,
    "definition_and_justification_impact_level": 750,
    "identification_of_the_intervention_area": 500,
    "early_action_selection_process": 2000,
    "evidence_base": 1200,
    "usefulness_of_actions": 500,
    "feasibility": 500,
    "early_action_implementation_process": 750,
    "trigger_activation_system": 500,
    "selection_of_target_population": 500,
    "stop_mechanism": 500,
    "meal": 1200,
    "operational_administrative_capacity": 1200,
    "strategies_and_plans": 500,
    "advance_financial_capacity": 300,
    "budget_description": 500,
    "readiness_cost_description": 500,
    "prepositioning_cost_description": 500,
    "early_action_cost_description": 500,
    "eap_endorsement": 300,
}


def word_count(value: str) -> int:
    return len(WORD_PATTERN.findall(value))


def validate_narrative_limits(payload: Mapping[str, Any], eap_kind: str) -> None:
    limits = SIMPLIFIED_WORD_LIMITS if eap_kind == "simplified" else FULL_WORD_LIMITS
    for field, limit in limits.items():
        value = payload.get(field)
        if isinstance(value, str):
            _check(field, value, limit)

    if eap_kind == "full":
        key_actors = payload.get("key_actors")
        if isinstance(key_actors, list):
            for index, item in enumerate(key_actors):
                if isinstance(item, Mapping) and isinstance(item.get("description"), str):
                    _check(f"key_actors.{index}.description", item["description"], 150)
        impacts = payload.get("prioritized_impacts")
        if isinstance(impacts, list):
            for index, item in enumerate(impacts):
                if isinstance(item, Mapping) and isinstance(item.get("impact"), str):
                    _check(f"prioritized_impacts.{index}.impact", item["impact"], 500)


def _check(path: str, value: str, limit: int) -> None:
    count = word_count(value)
    if count > limit:
        raise ValidationFailure(
            f"{path} contains {count} words; the maximum is {limit}. "
            "The text was not truncated."
        )
