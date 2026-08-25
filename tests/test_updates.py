import copy
import json
from pathlib import Path

import pytest

from eap_migration.exceptions import RecoveryRequired, SafetyError
from eap_migration.updates import (
    UpdateEngine,
    apply_changes,
    load_change_document,
    load_update_plan,
    writable_payload,
)

ROOT = Path(__file__).parents[1]


def snapshot_bodies() -> tuple[dict, dict]:
    document = json.loads(
        (ROOT / "schemas" / "staging-reference-catalog.2026-08-25.json").read_text(
            encoding="utf-8"
        )
    )
    return document["global_enums"], document["eap_options"]


def openapi_document() -> dict:
    return json.loads(
        (ROOT / "schemas" / "staging-openapi.2026-08-25.json").read_text(
            encoding="utf-8"
        )
    )


def current_application() -> dict:
    return {
        "id": 23,
        "eap_registration": 37,
        "modified_at": "2026-08-25T10:00:00Z",
        "version": 1,
        "is_locked": False,
        "seap_timeframe": 2,
        "seap_lead_timeframe_unit": 30,
        "seap_lead_time": 5,
        "activation_timeframe_unit": 20,
        "activation_timeframe": 3,
        "trigger_statement": "Old trigger",
        "partner_contacts": [
            {"id": 8, "name": "Partner", "title": "Old title", "email": "p@example.org"}
        ],
        "planned_operations": [
            {
                "id": 54,
                "sector": 101,
                "people_targeted": 10,
                "budget_per_sector": 100,
                "indicators": [],
                "readiness_activities": [],
                "prepositioning_activities": [],
                "early_action_activities": [],
            }
        ],
        "enabling_approaches": [
            {
                "id": 2,
                "approach": 10,
                "budget_per_approach": 100,
                "indicators": [],
                "readiness_activities": [],
                "prepositioning_activities": [],
                "early_action_activities": [],
            }
        ],
        "created_at": "2026-08-25T09:00:00Z",
        "created_by": 9634,
        "created_by_details": {"id": 9634},
        "modified_by": 9634,
        "modified_by_details": {"id": 9634},
        "partners_details": [],
    }


class FakeUpdateClient:
    def __init__(self) -> None:
        self.application = current_application()
        self.calls: list[tuple[str, str, dict | None]] = []
        self.last_correlation_id = None

    def get(self, path: str, **kwargs):
        self.calls.append(("GET", path, kwargs or None))
        if path == "global-enums/":
            return snapshot_bodies()[0]
        if path == "eap/options/":
            return snapshot_bodies()[1]
        if path == "https://goadmin-stage.ifrc.org/api-docs/?format=json":
            return openapi_document()
        if path == "/simplified-eap/23/":
            return copy.deepcopy(self.application)
        if path == "/eap-registration/37/":
            return {"id": 37, "status": 10, "status_display": "Under Development"}
        raise AssertionError(f"unexpected GET {path}")

    def patch_json(self, path: str, payload: dict):
        self.calls.append(("PATCH", path, payload))
        self.application.update(copy.deepcopy(payload))
        self.application["modified_at"] = "2026-08-25T10:05:00Z"
        return copy.deepcopy(self.application)


def test_writable_payload_removes_server_fields_but_keeps_nested_ids() -> None:
    payload = writable_payload(current_application())

    assert "id" not in payload
    assert "created_at" not in payload
    assert "modified_at" not in payload
    assert payload["planned_operations"][0]["id"] == 54
    assert "created_by_details" not in payload


def test_apply_changes_supports_narrative_and_list_operations() -> None:
    final = apply_changes(
        current_application(),
        {
            "trigger_statement": {"set": "New trigger"},
            "planned_operations": {
                "add": [
                    {
                        "sector": 102,
                        "people_targeted": 20,
                        "budget_per_sector": 200,
                        "indicators": [],
                        "readiness_activities": [],
                        "prepositioning_activities": [],
                        "early_action_activities": [],
                    }
                ]
            },
            "partner_contacts": {
                "update": [
                    {"match": {"id": 8}, "set": {"title": "New title"}}
                ]
            },
            "enabling_approaches": {"remove": [{"id": 2}]},
        },
    )

    assert final["trigger_statement"] == "New trigger"
    assert len(final["planned_operations"]) == 2
    assert final["partner_contacts"][0]["title"] == "New title"
    assert final["enabling_approaches"] == []


def test_update_plan_contains_complete_final_payload(tmp_path: Path) -> None:
    client = FakeUpdateClient()
    plan = UpdateEngine(client, 23, "simplified").prepare(
        load_change_document(
            _write_changes(
                tmp_path,
                {
                    "trigger_statement": {"set": "New trigger"},
                    "planned_operations": {
                        "add": [
                            {
                                "sector": 102,
                                "people_targeted": 20,
                                "budget_per_sector": 200,
                                "indicators": [],
                                "readiness_activities": [],
                                "prepositioning_activities": [],
                                "early_action_activities": [],
                            }
                        ]
                    },
                },
            )
        )
    )

    assert plan.final_payload["trigger_statement"] == "New trigger"
    assert len(plan.final_payload["planned_operations"]) == 2
    assert set(plan.patch_payload) == {"trigger_statement", "planned_operations"}
    assert "seap_timeframe" not in plan.patch_payload
    assert plan.base_modified_at == "2026-08-25T10:00:00Z"
    assert plan.differences


def test_update_plan_round_trips_from_review_artifact(tmp_path: Path) -> None:
    client = FakeUpdateClient()
    plan = UpdateEngine(client, 23, "simplified").prepare(
        load_change_document(
            _write_changes(tmp_path, {"trigger_statement": {"set": "New trigger"}})
        )
    )
    plan_path = tmp_path / "update-plan.json"
    plan_path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")

    loaded = load_update_plan(plan_path)

    assert loaded.final_payload == plan.final_payload
    assert loaded.patch_payload == {"trigger_statement": "New trigger"}


def test_update_apply_stops_when_modified_at_is_stale(tmp_path: Path) -> None:
    client = FakeUpdateClient()
    plan = UpdateEngine(client, 23, "simplified").prepare(
        load_change_document(
            _write_changes(tmp_path, {"trigger_statement": {"set": "New trigger"}})
        )
    )
    client.application["modified_at"] = "2026-08-25T10:06:00Z"

    with pytest.raises(RecoveryRequired, match="changed after update planning"):
        UpdateEngine(client, 23, "simplified").apply(plan, tmp_path)
    assert not any(method == "PATCH" for method, _, _ in client.calls)


def test_update_apply_patches_once_and_verifies_final_state(tmp_path: Path) -> None:
    client = FakeUpdateClient()
    plan = UpdateEngine(client, 23, "simplified").prepare(
        load_change_document(
            _write_changes(tmp_path, {"trigger_statement": {"set": "New trigger"}})
        )
    )

    result = UpdateEngine(client, 23, "simplified").apply(plan, tmp_path)

    assert result["verification"]["ok"] is True
    assert sum(method == "PATCH" for method, _, _ in client.calls) == 1
    patch_payload = next(payload for method, _, payload in client.calls if method == "PATCH")
    assert patch_payload is not None
    assert set(patch_payload) == {"trigger_statement", "modified_at"}
    assert list(tmp_path.glob("eap-simplified-23-update-receipt.json"))


def test_update_rejects_locked_target(tmp_path: Path) -> None:
    client = FakeUpdateClient()
    client.application["is_locked"] = True

    with pytest.raises(SafetyError, match="unlocked"):
        UpdateEngine(client, 23, "simplified").prepare(
            load_change_document(
                _write_changes(tmp_path, {"trigger_statement": {"set": "New trigger"}})
            )
        )


def _write_changes(tmp_path: Path, changes: dict) -> Path:
    path = tmp_path / "changes.json"
    path.write_text(json.dumps({"changes": changes}), encoding="utf-8")
    return path
