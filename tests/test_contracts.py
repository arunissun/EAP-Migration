import copy
import json
from pathlib import Path

import pytest

from eap_migration.contracts import validate_openapi_contract
from eap_migration.exceptions import ValidationFailure

ROOT = Path(__file__).parents[1]


def openapi_document() -> dict:
    return json.loads(
        (ROOT / "schemas" / "staging-openapi.2026-08-25.json").read_text(
            encoding="utf-8"
        )
    )


def test_openapi_snapshot_matches_simplified_local_contract() -> None:
    report = validate_openapi_contract(openapi_document(), "simplified")

    assert report["caption_max_length"] == 225
    assert report["modified_at_type"] == "string"


def test_openapi_snapshot_matches_full_local_contract() -> None:
    report = validate_openapi_contract(openapi_document(), "full")

    assert report["application_schema"] == "FullEAP"


def test_openapi_drift_reports_missing_writable_field() -> None:
    document = copy.deepcopy(openapi_document())
    document["components"]["schemas"]["SimplifiedEAP"]["properties"].pop("trigger_statement")

    with pytest.raises(ValidationFailure, match="missing local writable fields"):
        validate_openapi_contract(document, "simplified")


def test_openapi_drift_reports_caption_constraint_change() -> None:
    document = copy.deepcopy(openapi_document())
    document["components"]["schemas"]["EAPFile"]["properties"]["caption"]["maxLength"] = 255

    with pytest.raises(ValidationFailure, match="EAPFile.caption"):
        validate_openapi_contract(document, "simplified")
