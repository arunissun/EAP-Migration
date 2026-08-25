import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from eap_migration.exceptions import ValidationFailure
from eap_migration.files import (
    DEFAULT_MAX_FILE_SIZE,
    validate_application_file_payload,
    validate_files,
)
from eap_migration.models.common import FileSpec


def write_file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"file contents")
    return path


def test_raster_image_is_allowed_for_evidence(tmp_path: Path) -> None:
    path = write_file(tmp_path, "map.png")

    result = validate_files(
        tmp_path / "case.json",
        {"evidence": FileSpec(path=str(path), caption="Evidence")},
        ["evidence"],
    )

    assert result["evidence"].path == path.resolve()


def test_svg_is_rejected_everywhere(tmp_path: Path) -> None:
    path = write_file(tmp_path, "map.svg")

    with pytest.raises(ValidationFailure, match="SVG"):
        validate_files(
            tmp_path / "case.json",
            {"evidence": FileSpec(path=str(path), caption="Evidence")},
            ["evidence"],
        )


def test_cover_must_be_a_raster_image(tmp_path: Path) -> None:
    path = write_file(tmp_path, "cover.pdf")

    with pytest.raises(ValidationFailure, match="cover"):
        validate_files(
            tmp_path / "case.json",
            {"cover": FileSpec(path=str(path), caption="Cover")},
            ["cover"],
        )


def test_budget_must_use_a_restricted_document_type(tmp_path: Path) -> None:
    path = write_file(tmp_path, "budget.png")

    with pytest.raises(ValidationFailure, match="budget"):
        validate_files(
            tmp_path / "case.json",
            {"budget": FileSpec(path=str(path), caption="Budget")},
            ["budget"],
        )


def test_unreferenced_optional_file_is_not_inventoried_or_uploaded(tmp_path: Path) -> None:
    referenced = write_file(tmp_path, "evidence.pdf")
    optional = write_file(tmp_path, "unused.pdf")

    result = validate_files(
        tmp_path / "case.json",
        {
            "evidence": FileSpec(path=str(referenced), caption="Evidence"),
            "unused": FileSpec(path=str(optional), caption="Unused", required=False),
        },
        ["evidence"],
    )

    assert set(result) == {"evidence"}


def test_caption_limit_matches_live_schema() -> None:
    FileSpec(path="evidence.pdf", caption="x" * 225)

    with pytest.raises(ValidationError):
        FileSpec(path="evidence.pdf", caption="x" * 226)


def test_file_size_boundaries_match_api_limit(monkeypatch, tmp_path: Path) -> None:
    path = write_file(tmp_path, "evidence.pdf")
    monkeypatch.setattr("eap_migration.files.sha256_file", lambda _: "a" * 64)

    for size in (25 * 1024 * 1024, DEFAULT_MAX_FILE_SIZE):
        os.truncate(path, size)
        assert "evidence" in validate_files(
            tmp_path / "case.json",
            {"evidence": FileSpec(path=str(path), caption="Evidence")},
            ["evidence"],
        )

    os.truncate(path, 0)
    with pytest.raises(ValidationFailure):
        validate_files(
            tmp_path / "case.json",
            {"evidence": FileSpec(path=str(path), caption="Evidence")},
            ["evidence"],
        )

    os.truncate(path, DEFAULT_MAX_FILE_SIZE + 1)
    with pytest.raises(ValidationFailure):
        validate_files(
            tmp_path / "case.json",
            {"evidence": FileSpec(path=str(path), caption="Evidence")},
            ["evidence"],
        )


def test_captioned_application_array_has_five_file_limit() -> None:
    with pytest.raises(ValidationFailure, match="maximum is 5"):
        validate_application_file_payload(
            {"hazard_impact_files": [{"id": index, "caption": "x"} for index in range(6)]}
        )
