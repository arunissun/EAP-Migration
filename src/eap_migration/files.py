"""Local attachment validation and hashing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .exceptions import ValidationFailure
from .models.common import FileSpec
from .state import sha256_file

DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".xlsm"}
RASTER_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
COVER_EXTENSIONS = RASTER_EXTENSIONS
EVIDENCE_EXTENSIONS = DOCUMENT_EXTENSIONS | RASTER_EXTENSIONS
DEFAULT_MAX_FILE_SIZE = 100 * 1024 * 1024
CAPTIONED_APPLICATION_FILE_FIELDS = {
    "hazard_impact_files",
    "risk_selected_protocols_files",
    "selected_early_actions_files",
    "hazard_selection_files",
    "exposed_element_and_vulnerability_factor_files",
    "prioritized_impact_files",
    "forecast_selection_files",
    "definition_and_justification_impact_level_files",
    "identification_of_the_intervention_area_files",
    "early_action_selection_process_files",
    "early_action_implementation_files",
    "trigger_activation_system_files",
}


@dataclass(frozen=True, slots=True)
class LocalFile:
    key: str
    path: Path
    caption: str
    size: int
    sha256: str


def resolve_file_path(case_path: Path, spec: FileSpec) -> Path:
    path = Path(spec.path).expanduser()
    return path.resolve() if path.is_absolute() else (case_path.parent / path).resolve()


def validate_files(
    case_path: Path,
    manifest: dict[str, FileSpec],
    references: Iterable[str],
    *,
    max_size: int = DEFAULT_MAX_FILE_SIZE,
    field_names: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, LocalFile]:
    required_keys = set(references)
    unknown = sorted(required_keys - set(manifest))
    if unknown:
        raise ValidationFailure(
            "File references are missing from the manifest: " + ", ".join(unknown)
        )

    inventory: dict[str, LocalFile] = {}
    for key, spec in manifest.items():
        if key not in required_keys:
            continue
        path = resolve_file_path(case_path, spec)
        if not path.is_file():
            if spec.required or key in required_keys:
                raise ValidationFailure(f"Required local file does not exist: {path}")
            continue
        suffix = path.suffix.lower()
        if suffix == ".svg":
            fields = ", ".join(field_names.get(key, (key,)) if field_names else (key,))
            raise ValidationFailure(
                f"File '{key}' for {fields} "
                "is SVG; SVG files are not supported"
            )
        kind = spec.kind or _infer_file_kind(key)
        if kind == "cover":
            allowed = COVER_EXTENSIONS
        elif kind in {"budget", "checklist"}:
            allowed = DOCUMENT_EXTENSIONS
        else:
            allowed = EVIDENCE_EXTENSIONS
        if suffix not in allowed:
            fields = ", ".join(field_names.get(key, (key,)) if field_names else (key,))
            raise ValidationFailure(
                f"File '{key}' for {fields} has unsupported extension {path.suffix!r} "
                f"for {kind} files"
            )
        size = path.stat().st_size
        if size <= 0 or size > max_size:
            fields = ", ".join(field_names.get(key, (key,)) if field_names else (key,))
            raise ValidationFailure(
                f"File '{key}' for {fields} must be between 1 byte and {max_size} bytes"
            )
        inventory[key] = LocalFile(key, path, spec.caption, size, sha256_file(path))
    return inventory


def _infer_file_kind(key: str) -> str:
    normalized = key.casefold()
    if "cover" in normalized:
        return "cover"
    if "budget" in normalized:
        return "budget"
    if "checklist" in normalized:
        return "checklist"
    return "evidence"


def validate_application_file_payload(payload: Mapping[str, Any]) -> None:
    """Check serializer-managed captioned arrays in a raw application payload."""

    for field in CAPTIONED_APPLICATION_FILE_FIELDS:
        value = payload.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            raise ValidationFailure(f"Application field '{field}' must be a list")
        if len(value) > 5:
            raise ValidationFailure(
                f"Application field '{field}' contains {len(value)} files; the maximum is 5"
            )
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                raise ValidationFailure(f"Application field '{field}.{index}' must be an object")
            caption = item.get("caption")
            if caption is not None and (
                not isinstance(caption, str) or len(caption) > 225
            ):
                raise ValidationFailure(
                    f"Application field '{field}.{index}.caption' exceeds the 225-character limit"
                )
    cover = payload.get("cover_image_file")
    if isinstance(cover, Mapping):
        caption = cover.get("caption")
        if caption is not None and (not isinstance(caption, str) or len(caption) > 225):
            raise ValidationFailure(
                "Application field 'cover_image_file.caption' exceeds the 225-character limit"
            )


def all_file_references(application: Any) -> list[str]:
    return list(file_reference_fields(application).keys())


def file_reference_fields(application: Any) -> dict[str, list[str]]:
    """Return each logical file reference and the API fields that use it."""

    references: dict[str, list[str]] = {}
    values = (
        application.model_dump(mode="python")
        if hasattr(application, "model_dump")
        else vars(application)
    )
    for field_name, value in values.items():
        if not field_name.endswith("file") and not field_name.endswith("files"):
            continue
        if isinstance(value, str):
            references.setdefault(value, []).append(field_name)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    references.setdefault(item, []).append(field_name)
    return references
