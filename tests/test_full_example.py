from pathlib import Path

from eap_migration.adapters import FullEapAdapter, ResolvedContext
from eap_migration.case_loader import load_case
from eap_migration.models.full import FullEapApplication
from eap_migration.settings import Settings

ROOT = Path(__file__).parents[1]
CASE_PATH = ROOT / "cases" / "fiji-cyclone-full-eap.example.json"


def test_form_complete_full_eap_examples_match_the_runnable_case(monkeypatch) -> None:
    monkeypatch.setenv("GO_EAP_CONTACT_EMAIL", "training.full-eap@example.org")
    monkeypatch.setenv("GO_EAP_CONTACT_PHONE", "+679 000 0000")
    case, _ = load_case(CASE_PATH, Settings())
    adapter = FullEapAdapter()
    adapter.validate_case(case)

    context = ResolvedContext(
        registration_id=1001,
        admin2_ids=[],
        file_ids={key: 1100 + index for index, key in enumerate(case.files)},
        file_captions={key: spec.caption for key, spec in case.files.items()},
    )
    application_payload = adapter.build_create_payload(case, context)

    assert case.registration.eap_type == 10
    assert application_payload["eap_registration"] == 1001
    assert set(application_payload) == {
        *FullEapApplication.model_fields,
        "eap_registration",
        "admin2",
    }
