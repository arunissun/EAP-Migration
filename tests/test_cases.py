from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from eap_migration.adapters import FullEapAdapter, ResolvedContext, SimplifiedEapAdapter
from eap_migration.case_loader import expand_environment_placeholders, load_case
from eap_migration.exceptions import ValidationFailure
from eap_migration.models import FullCase, SimplifiedCase
from eap_migration.models.common import EarlyAction
from eap_migration.settings import Settings

ROOT = Path(__file__).parents[1]


def test_environment_expansion_leaves_file_references_alone() -> None:
    expanded = expand_environment_placeholders(
        {"email": "${GO_EAP_CONTACT_EMAIL}", "file": "${file:budget}"},
        {"GO_EAP_CONTACT_EMAIL": "test@example.org"},
    )
    assert expanded == {"email": "test@example.org", "file": "${file:budget}"}


def test_fiji_simplified_case_validates_and_builds_payload(monkeypatch) -> None:
    monkeypatch.setenv("GO_EAP_CONTACT_EMAIL", "test@example.org")
    case, _ = load_case(ROOT / "cases" / "fiji-simplified-eap.json", Settings())
    adapter = SimplifiedEapAdapter()
    adapter.validate_case(case)
    context = ResolvedContext(
        registration_id=100,
        admin2_ids=[],
        file_ids={key: index + 1 for index, key in enumerate(case.files)},
        file_captions={key: spec.caption for key, spec in case.files.items()},
    )
    payload = adapter.build_create_payload(case, context)
    assert payload["eap_registration"] == 100
    assert payload["cover_image_file"] == {"id": 1, "caption": "Fiji cyclone cover"}
    assert payload["total_budget"] == 111812
    assert sum(row["budget_per_sector"] for row in payload["planned_operations"]) == 74306
    assert "status" not in payload


def test_full_synthetic_case_validates_and_builds_all_file_modes(monkeypatch) -> None:
    monkeypatch.setenv("GO_EAP_CONTACT_EMAIL", "test@example.org")
    case, _ = load_case(ROOT / "cases" / "full-eap.synthetic.example.json", Settings())
    adapter = FullEapAdapter()
    adapter.validate_case(case)
    context = ResolvedContext(
        registration_id=200,
        admin2_ids=[7],
        file_ids={key: index + 1 for index, key in enumerate(case.files)},
        file_captions={key: spec.caption for key, spec in case.files.items()},
    )
    payload = adapter.build_create_payload(case, context)
    assert payload["eap_registration"] == 200
    assert payload["admin2"] == [7]
    assert payload["forecast_table_file"] == context.file_ids["forecast_table"]
    assert payload["risk_analysis_relevant_files"] == [context.file_ids["risk_analysis_relevant"]]
    assert payload["hazard_selection_files"][0]["caption"] == "Hazard selection evidence"
    assert (
        payload["planned_operations"][0]["prepositioning_activities"][0]["activation_one"] is True
    )


def test_full_early_actions_require_action_and_reject_unknown_keys(monkeypatch) -> None:
    monkeypatch.setenv("GO_EAP_CONTACT_EMAIL", "test@example.org")
    case, _ = load_case(ROOT / "cases" / "full-eap.synthetic.example.json", Settings())
    assert isinstance(case, FullCase)
    adapter = TypeAdapter(list[EarlyAction])

    with pytest.raises(ValidationError):
        adapter.validate_python([{}])
    with pytest.raises(ValidationError):
        adapter.validate_python([{"action": "Valid", "unexpected": "value"}])

    actions = adapter.validate_python([{"action": "Valid action"}])
    assert actions[0].action == "Valid action"


def test_draft_profile_reports_missing_narrative_without_blocking(monkeypatch) -> None:
    monkeypatch.setenv("GO_EAP_CONTACT_EMAIL", "test@example.org")
    case, _ = load_case(ROOT / "cases" / "fiji-simplified-eap.json", Settings())
    assert isinstance(case, SimplifiedCase)
    case.application.trigger_statement = None

    adapter = SimplifiedEapAdapter()
    adapter.validate_case(case)
    payload = adapter.build_create_payload(
        case,
        ResolvedContext(
            registration_id=100,
            admin2_ids=[],
            file_ids={key: index + 1 for index, key in enumerate(case.files)},
            file_captions={key: spec.caption for key, spec in case.files.items()},
        ),
    )
    assert payload["trigger_statement"] is None

    case.completeness_profile = "strict"
    with pytest.raises(ValidationFailure, match="Strict completeness"):
        adapter.validate_case(case)
