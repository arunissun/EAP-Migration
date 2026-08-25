from pathlib import Path
from typing import Any, cast

import pytest

from eap_migration.case_loader import load_case
from eap_migration.client import EapApiClient
from eap_migration.exceptions import ApiError, StateError
from eap_migration.files import LocalFile
from eap_migration.models import SimplifiedCase
from eap_migration.orchestrator import MigrationEngine
from eap_migration.settings import Settings

ROOT = Path(__file__).parents[1]


class FileClient:
    def __init__(self, response: Any) -> None:
        self.response = response

    def get(self, _path: str) -> Any:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def close(self) -> None:
        pass


def make_engine(tmp_path: Path, client: FileClient, monkeypatch) -> MigrationEngine:
    monkeypatch.setenv("GO_EAP_CONTACT_EMAIL", "test@example.org")
    case, case_path = load_case(ROOT / "cases" / "fiji-simplified-eap.json", Settings())
    assert isinstance(case, SimplifiedCase)
    return MigrationEngine(
        case,
        case_path,
        cast(EapApiClient, client),
        state_root=tmp_path / ".state",
    )


def local_file(tmp_path: Path) -> LocalFile:
    path = tmp_path / "evidence.pdf"
    path.write_bytes(b"evidence")
    return LocalFile("evidence", path, "Evidence", path.stat().st_size, "a" * 64)


def test_matching_remote_file_identity_passes(monkeypatch, tmp_path: Path) -> None:
    engine = make_engine(
        tmp_path,
        FileClient(
            {"id": 12, "file": "https://storage.example/evidence.pdf", "caption": "Evidence"}
        ),
        monkeypatch,
    )
    try:
        metadata = engine._verify_known_file(12, "evidence", local_file(tmp_path))
    finally:
        engine.client.close()

    assert metadata["remote_basename"] == "evidence.pdf"
    assert metadata["remote_caption"] == "Evidence"


def test_wrong_remote_basename_stops(monkeypatch, tmp_path: Path) -> None:
    engine = make_engine(
        tmp_path,
        FileClient({"id": 12, "file": "https://storage.example/other.pdf", "caption": "Evidence"}),
        monkeypatch,
    )
    try:
        with pytest.raises(StateError, match="basename"):
            engine._verify_known_file(12, "evidence", local_file(tmp_path))
    finally:
        engine.client.close()


def test_wrong_remote_caption_stops(monkeypatch, tmp_path: Path) -> None:
    engine = make_engine(
        tmp_path,
        FileClient(
            {"id": 12, "file": "https://storage.example/evidence.pdf", "caption": "Other"}
        ),
        monkeypatch,
    )
    try:
        with pytest.raises(StateError, match="caption"):
            engine._verify_known_file(12, "evidence", local_file(tmp_path))
    finally:
        engine.client.close()


def test_missing_remote_file_is_stale_state(monkeypatch, tmp_path: Path) -> None:
    engine = make_engine(
        tmp_path,
        FileClient(ApiError("GET", "/eap-file/12/", 404, {"detail": "missing"})),
        monkeypatch,
    )
    try:
        with pytest.raises(ApiError, match="HTTP 404"):
            engine._verify_known_file(12, "evidence", local_file(tmp_path))
    finally:
        engine.client.close()
