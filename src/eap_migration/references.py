"""Shared reference lookup helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .exceptions import ApiError, StateError, ValidationFailure


def response_id(body: Any, resource_name: str) -> int:
    if not isinstance(body, dict):
        raise StateError(f"{resource_name} response was not a JSON object")
    value = body.get("id", body.get("pk"))
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StateError(f"{resource_name} response did not contain a positive integer ID")
    return value


def get_value(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def values_equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, list) and isinstance(actual, list):
        return expected == actual
    return expected == actual


def registration_signature_matches(
    row: dict[str, Any], expected: dict[str, Any], *, ignore: Iterable[str] = ()
) -> bool:
    ignored = set(ignore)
    for field in ("country", "national_society", "disaster_type", "eap_type"):
        if field in ignored:
            continue
        if get_value(row, field) != expected.get(field):
            return False
    for field in (
        "expected_submission_time",
        "national_society_contact_email",
        "national_society_contact_name",
    ):
        if field in ignored:
            continue
        if normalized_text(get_value(row, field)) != normalized_text(expected.get(field)):
            return False
    return True


def validate_registration_references(client: Any, registration: Any, application: Any) -> None:
    """Confirm every supplied registration foreign key through its detail endpoint."""

    references: list[tuple[str, str, int]] = [
        ("registration.country", "/country/{id}/", registration.country),
        ("registration.national_society", "/country/{id}/", registration.national_society),
        ("registration.disaster_type", "/disaster_type/{id}/", registration.disaster_type),
    ]
    references.extend(
        ("registration.partner", "/country/{id}/", value)
        for value in registration.partners
    )
    references.extend(
        ("registration.user", "/user/{id}/", value) for value in registration.users
    )
    references.extend(
        ("application.partner", "/country/{id}/", value) for value in application.partners
    )

    seen: set[tuple[str, int]] = set()
    for field, template, value in references:
        identity = (template, value)
        if identity in seen:
            continue
        seen.add(identity)
        try:
            body = client.get(template.format(id=value))
        except ApiError as exc:
            raise ValidationFailure(
                f"Reference validation failed for {field}={value}: HTTP {exc.status_code}"
            ) from exc
        if not isinstance(body, dict):
            raise ValidationFailure(
                f"Reference validation returned invalid data for {field}={value}"
            )
        returned_id = body.get("id", body.get("pk"))
        if returned_id != value:
            raise ValidationFailure(
                f"Reference validation returned the wrong ID for {field}={value}: "
                f"{returned_id!r}"
            )
