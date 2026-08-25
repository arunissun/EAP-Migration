from pathlib import Path

import pytest

from eap_migration.case_loader import load_case
from eap_migration.client import EapApiClient
from eap_migration.exceptions import SafetyError
from eap_migration.models import SimplifiedCase
from eap_migration.orchestrator import MigrationEngine
from eap_migration.settings import Settings

ROOT = Path(__file__).parents[1]


def make_engine(monkeypatch, tmp_path: Path) -> MigrationEngine:
    monkeypatch.setenv("GO_EAP_CONTACT_EMAIL", "test@example.org")
    case, case_path = load_case(ROOT / "cases" / "fiji-simplified-eap.json", Settings())
    assert isinstance(case, SimplifiedCase)
    return MigrationEngine(
        case,
        case_path,
        EapApiClient("secret-token"),
        state_root=tmp_path / ".state",
    )


def registration(engine: MigrationEngine) -> dict:
    return {
        **engine.case.registration.to_payload(),
        "id": 37,
        "status": 10,
        "status_display": "Under Development",
    }


def test_registration_invariants_compare_all_expected_fields(monkeypatch, tmp_path: Path) -> None:
    engine = make_engine(monkeypatch, tmp_path)
    try:
        report = engine._verify_registration_invariants(registration(engine))
    finally:
        engine.client.close()

    assert report.ok


def test_registration_wrong_numeric_status_fails_even_with_expected_display(
    monkeypatch, tmp_path: Path
) -> None:
    engine = make_engine(monkeypatch, tmp_path)
    try:
        remote = registration(engine)
        remote["status"] = 20
        with pytest.raises(SafetyError, match="numeric status 10"):
            engine._verify_registration_invariants(remote)
    finally:
        engine.client.close()


def test_registration_normalization_difference_fails(monkeypatch, tmp_path: Path) -> None:
    engine = make_engine(monkeypatch, tmp_path)
    try:
        remote = registration(engine)
        remote["national_society_contact_title"] = "Different"
        with pytest.raises(SafetyError, match="Registration verification failed"):
            engine._verify_registration_invariants(remote)
    finally:
        engine.client.close()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"version": 2}, "version 1"),
        ({"is_locked": True}, "unlocked"),
        ({"eap_registration": 99}, "wrong registration"),
    ],
)
def test_application_invariants_fail_for_unsafe_values(
    monkeypatch, tmp_path: Path, change: dict, message: str
) -> None:
    engine = make_engine(monkeypatch, tmp_path)
    try:
        remote = {"eap_registration": 37, "version": 1, "is_locked": False}
        remote.update(change)
        with pytest.raises(SafetyError, match=message):
            engine._verify_application_invariants(remote, 37)
    finally:
        engine.client.close()
