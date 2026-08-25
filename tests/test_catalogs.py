import copy
import json
from pathlib import Path

import pytest

from eap_migration.case_loader import load_case
from eap_migration.catalogs import ReferenceCatalog
from eap_migration.exceptions import ValidationFailure
from eap_migration.models import SimplifiedCase
from eap_migration.settings import Settings

ROOT = Path(__file__).parents[1]
SNAPSHOT = ROOT / "schemas" / "staging-reference-catalog.2026-08-25.json"


def catalog() -> ReferenceCatalog:
    document = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    return ReferenceCatalog.from_bodies(
        document["global_enums"],
        document["eap_options"],
        source="test-snapshot",
        captured_at=document["captured_at"],
    )


def simplified_case(monkeypatch) -> SimplifiedCase:
    monkeypatch.setenv("GO_EAP_CONTACT_EMAIL", "test@example.org")
    case, _ = load_case(ROOT / "cases" / "fiji-simplified-eap.json", Settings())
    assert isinstance(case, SimplifiedCase)
    return case


def test_snapshot_catalog_accepts_the_simplified_case(monkeypatch) -> None:
    catalog().validate_case(simplified_case(monkeypatch))


def test_catalog_rejects_unknown_sector(monkeypatch) -> None:
    case = simplified_case(monkeypatch)
    case.application.planned_operations[0].sector = 999

    with pytest.raises(ValidationFailure, match="sector catalog"):
        catalog().validate_case(case)


def test_catalog_rejects_invalid_hours_value(monkeypatch) -> None:
    case = simplified_case(monkeypatch)
    case.application.seap_lead_timeframe_unit = 40
    case.application.seap_lead_time = 1

    with pytest.raises(ValidationFailure, match="not allowed"):
        catalog().validate_case(case)


@pytest.mark.parametrize(
    ("timeframe", "invalid_value"),
    [(10, 6), (20, 13), (30, 32), (40, 1)],
)
def test_catalog_rejects_invalid_activity_value_for_each_unit(
    monkeypatch, timeframe: int, invalid_value: int
) -> None:
    case = simplified_case(monkeypatch)
    activity = case.application.planned_operations[0].readiness_activities[0]
    activity.timeframe = timeframe
    activity.time_value = [invalid_value]

    with pytest.raises(ValidationFailure, match="not allowed"):
        catalog().validate_case(case)


def test_catalog_rejects_activation_month_outside_live_catalog(monkeypatch) -> None:
    case = simplified_case(monkeypatch)
    case.application.activation_timeframe = 13

    with pytest.raises(ValidationFailure, match="not allowed"):
        catalog().validate_case(case)


def test_catalog_rejects_non_month_activation_unit(monkeypatch) -> None:
    case = simplified_case(monkeypatch)
    case.application.activation_timeframe_unit = 30

    with pytest.raises(ValidationFailure, match="Months"):
        catalog().validate_case(case)


def test_catalog_rejects_unknown_approach(monkeypatch) -> None:
    case = simplified_case(monkeypatch)
    case.application.enabling_approaches[0].approach = 999

    with pytest.raises(ValidationFailure, match="approach catalog"):
        catalog().validate_case(case)


def test_catalog_rejects_global_enum_and_options_mismatch() -> None:
    document = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    options = copy.deepcopy(document["eap_options"])
    options["sector_ap_codes"].pop("101")

    with pytest.raises(ValidationFailure, match="catalog conflict for sectors"):
        ReferenceCatalog.from_bodies(
            document["global_enums"],
            options,
            source="test",
            captured_at="test",
        )
