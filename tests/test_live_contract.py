import os

import pytest

from eap_migration.catalogs import fetch_reference_catalog
from eap_migration.client import EapApiClient
from eap_migration.contracts import validate_openapi_contract
from eap_migration.settings import Settings


@pytest.mark.skipif(
    not os.environ.get("EAP_LIVE_CONTRACT"),
    reason="set EAP_LIVE_CONTRACT=1 to run read-only staging contract checks",
)
def test_live_staging_contract_is_read_only() -> None:
    settings = Settings()
    with EapApiClient(
        settings.token_value(),
        timeout_seconds=settings.timeout_seconds,
        get_retries=settings.get_retries,
    ) as client:
        document = client.get("https://goadmin-stage.ifrc.org/api-docs/?format=json")
        validate_openapi_contract(document, "simplified")
        validate_openapi_contract(document, "full")
        catalog = fetch_reference_catalog(client)

    assert catalog.eap_types == frozenset({10, 20})
