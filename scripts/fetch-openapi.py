"""Fetch the staging OpenAPI document with a GET-only authenticated request."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from eap_migration.client import EapApiClient
from eap_migration.settings import STAGE_ORIGIN, Settings
from eap_migration.state import atomic_write_json


def main() -> None:
    settings = Settings()
    token = (
        settings.api_token.get_secret_value().strip()
        if settings.api_token is not None
        else "public-openapi-fetch"
    ) or "public-openapi-fetch"
    with EapApiClient(
        token,
        timeout_seconds=settings.timeout_seconds,
        get_retries=settings.get_retries,
    ) as client:
        document = client.get(f"{STAGE_ORIGIN}/api-docs/?format=json")
    if not isinstance(document, dict):
        raise RuntimeError("The OpenAPI endpoint did not return a JSON object")
    captured_at = datetime.now(UTC).date().isoformat()
    output = Path(__file__).parents[1] / "schemas" / f"staging-openapi.{captured_at}.json"
    atomic_write_json(output, document)
    print(f"Wrote GET-only OpenAPI snapshot to {output}")


if __name__ == "__main__":
    main()
