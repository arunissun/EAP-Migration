from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from eap_migration.case_loader import load_case
from eap_migration.client import EapApiClient
from eap_migration.exceptions import RecoveryRequired
from eap_migration.models import SimplifiedCase
from eap_migration.orchestrator import MigrationEngine, PlanResult
from eap_migration.settings import Settings
from eap_migration.state import StateRecord, payload_sha256

ROOT = Path(__file__).parents[1]


class RecoveryClient:
    def __init__(self, registrations: list[dict], applications: list[dict] | None = None) -> None:
        self.registrations = registrations
        self.applications = applications or []
        self.calls: list[tuple[str, str]] = []

    def get_paginated(self, path_or_url: str, *, params: dict):
        self.calls.append(("GET_PAGE", path_or_url))
        if path_or_url == "/eap-registration/":
            return self.registrations, len(self.registrations)
        if path_or_url == "/simplified-eap/":
            return self.applications, len(self.applications)
        raise AssertionError(path_or_url)


class AmbiguousWriteClient:
    def __init__(self) -> None:
        self.post_calls = 0
        self.last_correlation_id = None

    def post_json(self, _path: str, _payload: dict) -> dict:
        self.post_calls += 1
        raise RecoveryRequired("registration outcome is ambiguous")


def make_engine(monkeypatch, client: Any, tmp_path: Path) -> MigrationEngine:
    monkeypatch.setenv("GO_EAP_CONTACT_EMAIL", "test@example.org")
    case, case_path = load_case(ROOT / "cases" / "fiji-simplified-eap.json", Settings())
    assert isinstance(case, SimplifiedCase)
    return MigrationEngine(
        case,
        case_path,
        cast(EapApiClient, client),
        state_root=tmp_path / ".state",
    )


def pending_state(engine: MigrationEngine, *, intent_hash: str | None = None) -> StateRecord:
    expected = engine.case.registration.to_payload()
    return StateRecord(
        migration_key=engine.case.migration_key,
        case_sha256=engine._case_sha256(),
        registration_intent_sha256=intent_hash or payload_sha256(expected),
        registration_intent_created_at="2026-08-25T10:00:00Z",
        registration_recovery_required=True,
    )


def candidate(engine: MigrationEngine, registration_id: int = 987) -> dict:
    return {
        **engine.case.registration.to_payload(),
        "id": registration_id,
        "status": 10,
        "status_display": "Under Development",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def test_recovery_adopts_exactly_one_recent_candidate_without_second_post(
    monkeypatch, tmp_path: Path
) -> None:
    client = RecoveryClient([])
    engine = make_engine(monkeypatch, client, tmp_path)
    state = pending_state(engine)
    client.registrations = [candidate(engine)]
    recovered, conflicts = engine._recover_registration(
        state, engine.case.registration.to_payload()
    )

    assert recovered is not None
    assert recovered["id"] == 987
    assert conflicts == []
    assert not any(method == "POST" for method, _ in client.calls)


def test_recovery_zero_matches_stops(monkeypatch, tmp_path: Path) -> None:
    client = RecoveryClient([])
    engine = make_engine(monkeypatch, client, tmp_path)

    recovered, conflicts = engine._recover_registration(
        pending_state(engine), engine.case.registration.to_payload()
    )

    assert recovered is None
    assert "no matching candidate" in conflicts[0]


def test_recovery_multiple_matches_stops(monkeypatch, tmp_path: Path) -> None:
    client = RecoveryClient([])
    engine = make_engine(monkeypatch, client, tmp_path)
    client.registrations = [candidate(engine, 987), candidate(engine, 988)]

    recovered, conflicts = engine._recover_registration(
        pending_state(engine), engine.case.registration.to_payload()
    )

    assert recovered is None
    assert "2 matching registration candidates" in conflicts[0]


def test_recovery_changed_request_hash_stops_before_get(monkeypatch, tmp_path: Path) -> None:
    client = RecoveryClient([])
    engine = make_engine(monkeypatch, client, tmp_path)

    recovered, conflicts = engine._recover_registration(
        pending_state(engine, intent_hash="0" * 64), engine.case.registration.to_payload()
    )

    assert recovered is None
    assert "request hash" in conflicts[0]
    assert client.calls == []


def test_recovery_candidate_with_existing_application_stops(monkeypatch, tmp_path: Path) -> None:
    client = RecoveryClient([])
    engine = make_engine(monkeypatch, client, tmp_path)
    recovered_candidate = candidate(engine)
    client.registrations = [recovered_candidate]
    client.applications = [{"id": 1}]

    recovered, conflicts = engine._recover_registration(
        pending_state(engine), engine.case.registration.to_payload()
    )

    assert recovered is None
    assert "already has an application" in conflicts[0]


def test_existing_record_without_pending_intent_is_not_recovered(
    monkeypatch, tmp_path: Path
) -> None:
    client = RecoveryClient([])
    engine = make_engine(monkeypatch, client, tmp_path)
    client.registrations = [candidate(engine)]
    state = StateRecord(
        migration_key=engine.case.migration_key,
        case_sha256=engine._case_sha256(),
        registration_recovery_required=False,
    )

    recovered, conflicts = engine._recover_registration(
        state, engine.case.registration.to_payload()
    )

    assert recovered is None
    assert conflicts == []
    assert client.calls == []


def test_new_state_persists_recovered_registration_id(monkeypatch, tmp_path: Path) -> None:
    client = RecoveryClient([])
    engine = make_engine(monkeypatch, client, tmp_path)
    state = pending_state(engine)
    recovered = candidate(engine)
    plan = PlanResult(
        case=engine.case,
        adapter=engine.adapter,
        case_sha256=state.case_sha256,
        files={},
        admin2_ids=[],
        admin2_meta={},
        state=state,
        registration_id=987,
        application_id=None,
        existing_registration=recovered,
        existing_application=None,
        recovered_registration=recovered,
        catalog_summary={},
        contract_summary={},
        completeness_warnings=[],
        conflicts=[],
    )

    new_state = engine._new_state(plan)

    assert new_state.registration_id == 987
    assert new_state.registration_recovery_required is False
    assert new_state.registration_intent_sha256 is None


def test_ambiguous_registration_post_persists_intent_and_is_attempted_once(
    monkeypatch, tmp_path: Path
) -> None:
    client = AmbiguousWriteClient()
    engine = make_engine(monkeypatch, client, tmp_path)
    state = StateRecord(
        migration_key=engine.case.migration_key,
        case_sha256=engine._case_sha256(),
    )
    plan = PlanResult(
        case=engine.case,
        adapter=engine.adapter,
        case_sha256=state.case_sha256,
        files={},
        admin2_ids=[],
        admin2_meta={},
        state=state,
        registration_id=None,
        application_id=None,
        existing_registration=None,
        existing_application=None,
        recovered_registration=None,
        catalog_summary={},
        contract_summary={},
        completeness_warnings=[],
        conflicts=[],
    )
    monkeypatch.setattr(engine, "plan", lambda: plan)

    with pytest.raises(RecoveryRequired):
        engine.apply(confirm_stage_writes=True)

    saved = engine.state_store.load(engine.case.migration_key)
    assert saved is not None
    assert saved.registration_recovery_required is True
    assert saved.registration_intent_sha256 == payload_sha256(
        engine.case.registration.to_payload()
    )
    assert client.post_calls == 1
