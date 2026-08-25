"""Paginated, country-scoped Admin2 resolution."""

from __future__ import annotations

from typing import Any, Protocol

from .exceptions import ValidationFailure
from .models.common import Admin2Selection
from .references import normalized_text


class PaginatedClient(Protocol):
    def get_paginated(
        self, path_or_url: str, *, params: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], int | None]: ...


class Admin2Resolver:
    def __init__(self, client: PaginatedClient) -> None:
        self.client = client

    def resolve(self, selection: Admin2Selection) -> tuple[list[int], dict[str, Any]]:
        params: dict[str, Any] = {"limit": 100}
        if selection.country_iso3:
            key = (
                "admin1__country__iso3"
                if len(selection.country_iso3) == 1
                else "admin1__country__iso3__in"
            )
            params[key] = ",".join(selection.country_iso3)

        rows, count = self.client.get_paginated("/admin2/", params=params)
        ids: list[int] = []
        missing_ids: list[int] = []
        missing_codes: list[str] = []
        for requested_id in selection.include_ids:
            matches = [row for row in rows if row.get("id") == requested_id]
            if not matches:
                missing_ids.append(requested_id)
            else:
                ids.append(requested_id)

        for requested_code in selection.include_codes:
            matches = [
                row
                for row in rows
                if normalized_text(row.get("code")) == normalized_text(requested_code)
            ]
            if not matches:
                missing_codes.append(requested_code)
            else:
                ids.extend(int(row["id"]) for row in matches if isinstance(row.get("id"), int))

        ambiguous_names: list[str] = []
        missing_names: list[str] = []
        for requested_name in selection.include_names:
            matches = [
                row
                for row in rows
                if normalized_text(row.get("name")) == normalized_text(requested_name)
            ]
            if len(matches) > 1:
                ambiguous_names.append(requested_name)
            elif not matches:
                missing_names.append(requested_name)
            elif isinstance(matches[0].get("id"), int):
                ids.append(matches[0]["id"])

        if ambiguous_names:
            raise ValidationFailure(
                "Admin2 name selection is ambiguous; use an exact ID or code: "
                + ", ".join(ambiguous_names)
            )
        if missing_ids or missing_codes or missing_names:
            requested = [
                *(f"id={value}" for value in missing_ids),
                *(f"code={value}" for value in missing_codes),
                *(f"name={value}" for value in missing_names),
            ]
            if (
                selection.required
                or selection.include_ids
                or selection.include_codes
                or selection.include_names
            ):
                raise ValidationFailure(
                    "Admin2 selection did not resolve inside the country-scoped catalogue: "
                    + ", ".join(requested)
                )

        unique_ids = sorted(set(ids))
        if selection.required and not unique_ids:
            raise ValidationFailure("Admin2 selection is required but resolved to no rows")
        return unique_ids, {"count": count, "rows_returned": len(rows), "requested": params}
