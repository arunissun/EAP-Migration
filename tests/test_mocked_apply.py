import json
from pathlib import Path

import httpx
import respx

from eap_migration.adapters import FullEapAdapter, ResolvedContext, SimplifiedEapAdapter
from eap_migration.case_loader import load_case
from eap_migration.client import EapApiClient
from eap_migration.orchestrator import MigrationEngine
from eap_migration.settings import STAGE_API_BASE_URL, Settings

ROOT = Path(__file__).parents[1]


@respx.mock
def test_simplified_apply_persists_checkpoints_and_never_calls_status(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("GO_EAP_CONTACT_EMAIL", "test@example.org")
    case, case_path = load_case(ROOT / "cases" / "fiji-simplified-eap.json", Settings())
    adapter = SimplifiedEapAdapter()
    file_ids = {key: index + 1 for index, key in enumerate(case.files)}
    expected_application = adapter.build_create_payload(
        case,
        ResolvedContext(
            registration_id=100,
            admin2_ids=[],
            file_ids=file_ids,
            file_captions={key: spec.caption for key, spec in case.files.items()},
        ),
    )

    base = STAGE_API_BASE_URL
    snapshot = json.loads(
        (ROOT / "schemas" / "staging-reference-catalog.2026-08-25.json").read_text(
            encoding="utf-8"
        )
    )
    openapi = json.loads(
        (ROOT / "schemas" / "staging-openapi.2026-08-25.json").read_text(
            encoding="utf-8"
        )
    )
    respx.get("https://goadmin-stage.ifrc.org/api-docs/?format=json").mock(
        return_value=httpx.Response(200, json=openapi)
    )
    respx.get(f"{base}/eap/options/").mock(
        return_value=httpx.Response(200, json=snapshot["eap_options"])
    )
    respx.get(f"{base}/global-enums/").mock(
        return_value=httpx.Response(200, json=snapshot["global_enums"])
    )
    respx.get(f"{base}/admin2/").mock(
        return_value=httpx.Response(200, json={"count": 0, "next": None, "results": []})
    )
    for country_id in {66, 72, 84}:
        respx.get(f"{base}/country/{country_id}/").mock(
            return_value=httpx.Response(200, json={"id": country_id})
        )
    respx.get(f"{base}/disaster_type/4/").mock(
        return_value=httpx.Response(200, json={"id": 4})
    )
    respx.get(f"{base}/user/9634/").mock(
        return_value=httpx.Response(200, json={"id": 9634})
    )
    respx.get(f"{base}/eap-registration/").mock(
        return_value=httpx.Response(200, json={"count": 0, "next": None, "results": []})
    )

    uploaded = iter(file_ids.items())

    def upload_response(request: httpx.Request) -> httpx.Response:
        key, remote_id = next(uploaded)
        return httpx.Response(
            201,
            json={
                "id": remote_id,
                "file": f"https://storage.example/{Path(case.files[key].path).name}",
                "caption": case.files[key].caption,
            },
            request=request,
        )

    respx.post(f"{base}/eap-file/").mock(side_effect=upload_response)
    for key, remote_id in file_ids.items():
        respx.get(f"{base}/eap-file/{remote_id}/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": remote_id,
                    "file": f"https://storage.example/{Path(case.files[key].path).name}",
                    "caption": case.files[key].caption,
                },
            )
        )
    registration = {
        **case.registration.to_payload(),
        "id": 100,
        "status": 10,
        "status_display": "Under Development",
    }
    respx.post(f"{base}/eap-registration/").mock(return_value=httpx.Response(201, json={"id": 100}))
    respx.get(f"{base}/eap-registration/100/").mock(
        return_value=httpx.Response(200, json=registration)
    )
    respx.post(f"{base}/simplified-eap/").mock(return_value=httpx.Response(201, json={"id": 200}))
    respx.get(f"{base}/simplified-eap/200/").mock(
        return_value=httpx.Response(
            200,
            json={**expected_application, "id": 200, "version": 1, "is_locked": False},
        )
    )

    client = EapApiClient("secret-token")
    try:
        result = MigrationEngine(
            case,
            case_path,
            client,
            state_root=tmp_path / ".state",
            artifact_root=tmp_path / "artifacts",
        ).apply(confirm_stage_writes=True)
    finally:
        client.close()

    assert result["state"]["registration_id"] == 100
    assert result["state"]["application_id"] == 200
    assert (tmp_path / "artifacts" / "fiji-cyclone-seap-2026-receipt.json").is_file()
    receipt = json.loads(
        (tmp_path / "artifacts" / "fiji-cyclone-seap-2026-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["verification"]["registration_status"] == 10
    assert receipt["verification"]["application_version"] == 1
    assert receipt["verification"]["application_is_locked"] is False
    assert receipt["verification"]["application_registration_id"] == 100
    assert all(
        b"caption" in call.request.content and b"secret-token" not in call.request.content
        for call in respx.calls
        if call.request.method == "POST" and "/eap-file/" in str(call.request.url)
    )
    assert all("remote_basename" in item for item in result["state"]["files"].values())
    assert not any("/status/" in str(call.request.url) for call in respx.calls)


@respx.mock
def test_full_apply_builds_and_verifies_the_full_payload(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GO_EAP_CONTACT_EMAIL", "test@example.org")
    case, case_path = load_case(ROOT / "cases" / "full-eap.synthetic.example.json", Settings())
    adapter = FullEapAdapter()
    file_ids = {key: index + 100 for index, key in enumerate(case.files)}
    expected_application = adapter.build_create_payload(
        case,
        ResolvedContext(
            registration_id=100,
            admin2_ids=[],
            file_ids=file_ids,
            file_captions={key: spec.caption for key, spec in case.files.items()},
        ),
    )

    base = STAGE_API_BASE_URL
    snapshot = json.loads(
        (ROOT / "schemas" / "staging-reference-catalog.2026-08-25.json").read_text(
            encoding="utf-8"
        )
    )
    openapi = json.loads(
        (ROOT / "schemas" / "staging-openapi.2026-08-25.json").read_text(
            encoding="utf-8"
        )
    )
    respx.get("https://goadmin-stage.ifrc.org/api-docs/?format=json").mock(
        return_value=httpx.Response(200, json=openapi)
    )
    respx.get(f"{base}/eap/options/").mock(
        return_value=httpx.Response(200, json=snapshot["eap_options"])
    )
    respx.get(f"{base}/global-enums/").mock(
        return_value=httpx.Response(200, json=snapshot["global_enums"])
    )
    respx.get(f"{base}/admin2/").mock(
        return_value=httpx.Response(200, json={"count": 0, "next": None, "results": []})
    )
    for country_id in {66, 72, 84}:
        respx.get(f"{base}/country/{country_id}/").mock(
            return_value=httpx.Response(200, json={"id": country_id})
        )
    respx.get(f"{base}/disaster_type/4/").mock(
        return_value=httpx.Response(200, json={"id": 4})
    )
    respx.get(f"{base}/user/9634/").mock(
        return_value=httpx.Response(200, json={"id": 9634})
    )
    respx.get(f"{base}/eap-registration/").mock(
        return_value=httpx.Response(200, json={"count": 0, "next": None, "results": []})
    )

    uploaded = iter(file_ids.items())

    def upload_response(request: httpx.Request) -> httpx.Response:
        key, remote_id = next(uploaded)
        return httpx.Response(
            201,
            json={
                "id": remote_id,
                "file": f"https://storage.example/{Path(case.files[key].path).name}",
                "caption": case.files[key].caption,
            },
            request=request,
        )

    respx.post(f"{base}/eap-file/").mock(side_effect=upload_response)
    for key, remote_id in file_ids.items():
        respx.get(f"{base}/eap-file/{remote_id}/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": remote_id,
                    "file": f"https://storage.example/{Path(case.files[key].path).name}",
                    "caption": case.files[key].caption,
                },
            )
        )
    registration = {
        **case.registration.to_payload(),
        "id": 100,
        "status": 10,
        "status_display": "Under Development",
    }
    respx.post(f"{base}/eap-registration/").mock(
        return_value=httpx.Response(201, json={"id": 100})
    )
    respx.get(f"{base}/eap-registration/100/").mock(
        return_value=httpx.Response(200, json=registration)
    )
    respx.post(f"{base}/full-eap/").mock(return_value=httpx.Response(201, json={"id": 200}))
    respx.get(f"{base}/full-eap/200/").mock(
        return_value=httpx.Response(
            200,
            json={**expected_application, "id": 200, "version": 1, "is_locked": False},
        )
    )

    client = EapApiClient("secret-token")
    try:
        result = MigrationEngine(
            case,
            case_path,
            client,
            state_root=tmp_path / ".state",
            artifact_root=tmp_path / "artifacts",
        ).apply(confirm_stage_writes=True)
    finally:
        client.close()

    assert result["state"]["application_kind"] == "full"
    assert result["state"]["application_id"] == 200
    assert not any("/status/" in str(call.request.url) for call in respx.calls)
