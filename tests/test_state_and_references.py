from pathlib import Path
from typing import Any

import pytest

from eap_migration.admin2 import Admin2Resolver
from eap_migration.exceptions import ApiError, StateError, ValidationFailure
from eap_migration.models.common import Admin2Selection
from eap_migration.paths import (
    default_artifact_root,
    default_state_root,
    find_repository_root,
    legacy_state_path,
)
from eap_migration.references import validate_registration_references
from eap_migration.state import FileState, StateRecord, StateStore, _replace_with_bounded_retry

ROOT = Path(__file__).parents[1]


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def get_paginated(
        self, path_or_url: str, *, params: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], int | None]:
        self.last = (path_or_url, params)
        return self.rows, len(self.rows)


class ReferenceClient:
    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses

    def get(self, path: str) -> dict:
        if path not in self.responses:
            raise ApiError("GET", path, 404, {"detail": "not found"})
        return self.responses[path]


def test_admin2_resolver_uses_exact_country_scoped_matching() -> None:
    client = FakeClient(
        [
            {"id": 1, "code": "FJI-001", "name": "Suva"},
            {"id": 2, "code": "FJI-002", "name": "Nadi"},
        ]
    )
    ids, meta = Admin2Resolver(client).resolve(
        Admin2Selection(country_iso3=["fji"], include_codes=["FJI-002"])
    )
    assert ids == [2]
    assert meta["requested"]["admin1__country__iso3"] == "FJI"


def test_admin2_resolver_rejects_ambiguous_names() -> None:
    client = FakeClient([{"id": 1, "name": "Suva"}, {"id": 2, "name": " Suva "}])
    with pytest.raises(ValidationFailure, match="ambiguous"):
        Admin2Resolver(client).resolve(Admin2Selection(include_names=["suva"]))


def test_state_save_is_loadable_and_reset_requires_confirmation(tmp_path: Path) -> None:
    store = StateStore(tmp_path / ".state")
    state = StateRecord(
        migration_key="case-1",
        case_sha256="a" * 64,
        files={"budget": FileState(sha256="b" * 64, remote_id=12)},
        registration_id=10,
    )
    store.save(state)
    loaded = store.load("case-1")
    assert loaded is not None
    assert loaded.model_dump() == state.model_dump()
    with pytest.raises(StateError, match="--confirm"):
        store.reset("case-1", confirm=False)
    store.reset("case-1", confirm=True)
    assert store.load("case-1") is None


def test_repository_roots_are_deterministic_and_legacy_path_is_explicit() -> None:
    repository_root = find_repository_root(ROOT / "cases" / "fiji-simplified-eap.json")

    assert repository_root == ROOT
    assert default_state_root(repository_root) == ROOT / ".state"
    assert default_artifact_root(repository_root) == ROOT / "artifacts"
    assert legacy_state_path(
        ROOT / "cases" / "fiji-simplified-eap.json", "fiji-cyclone-seap-2026"
    ) == ROOT / "cases" / ".state" / "fiji-cyclone-seap-2026.json"


def test_registration_foreign_keys_are_checked_by_detail_endpoint() -> None:
    registration = type(
        "Registration",
        (),
        {
            "country": 66,
            "national_society": 66,
            "disaster_type": 4,
            "partners": [72],
            "users": [9634],
        },
    )()
    application = type("Application", (), {"partners": [72]})()
    client = ReferenceClient(
        {
            "/country/66/": {"id": 66},
            "/country/72/": {"id": 72},
            "/disaster_type/4/": {"id": 4},
            "/user/9634/": {"id": 9634},
        }
    )

    validate_registration_references(client, registration, application)


def test_missing_registration_foreign_key_is_actionable() -> None:
    registration = type(
        "Registration",
        (),
        {
            "country": 66,
            "national_society": 66,
            "disaster_type": 999,
            "partners": [],
            "users": [],
        },
    )()
    application = type("Application", (), {"partners": []})()

    with pytest.raises(ValidationFailure, match="disaster_type=999"):
        validate_registration_references(
            ReferenceClient({"/country/66/": {"id": 66}}), registration, application
        )


def test_atomic_replace_retries_a_transient_windows_lock(monkeypatch) -> None:
    attempts = 0

    def replace(_temporary, _destination) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("transient lock")

    monkeypatch.setattr("eap_migration.state.os.replace", replace)
    monkeypatch.setattr("eap_migration.state.time.sleep", lambda _: None)

    _replace_with_bounded_retry(Path("temporary"), Path("destination"))

    assert attempts == 2
