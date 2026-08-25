import httpx
import pytest

from eap_migration.client import EapApiClient
from eap_migration.exceptions import ApiError, RecoveryRequired


def test_get_connection_failure_is_retried(monkeypatch) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr("eap_migration.client.time.sleep", lambda _: None)
    client = EapApiClient("secret", get_retries=2, transport=httpx.MockTransport(handler))
    try:
        assert client.get("health/") == {"ok": True}
    finally:
        client.close()
    assert attempts == 2


def test_get_connection_failure_stops_after_configured_attempts(monkeypatch) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr("eap_migration.client.time.sleep", lambda _: None)
    client = EapApiClient("secret", get_retries=2, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(Exception, match="after 3 attempt"):
            client.get("health/")
    finally:
        client.close()
    assert attempts == 3


def test_get_retries_429_with_retry_after(monkeypatch) -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr("eap_migration.client.time.sleep", delays.append)
    client = EapApiClient("secret", get_retries=1, transport=httpx.MockTransport(handler))
    try:
        assert client.get("health/") == {"ok": True}
    finally:
        client.close()
    assert attempts == 2
    assert delays == [1.0]


def test_get_transient_500_is_retried(monkeypatch) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(500, json={"detail": "temporary"}, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr("eap_migration.client.time.sleep", lambda _: None)
    client = EapApiClient("secret", get_retries=1, transport=httpx.MockTransport(handler))
    try:
        assert client.get("health/") == {"ok": True}
    finally:
        client.close()
    assert attempts == 2


def test_post_connection_failure_is_not_retried() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("offline", request=request)

    client = EapApiClient("secret", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RecoveryRequired):
            client.post_json("resource/", {"value": 1})
    finally:
        client.close()
    assert attempts == 1


def test_get_retry_exhaustion_keeps_http_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "unavailable"}, request=request)

    client = EapApiClient("secret", get_retries=0, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ApiError) as raised:
            client.get("health/")
    finally:
        client.close()
    assert raised.value.status_code == 503


@pytest.mark.parametrize("status_code", [400, 401, 403, 409])
def test_http_errors_are_redacted_and_keep_status(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers={"X-Request-ID": "request-123"},
            json={"detail": "secret-token", "status": status_code},
            request=request,
        )

    client = EapApiClient("secret-token", get_retries=0, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ApiError) as raised:
            client.get("health/")
    finally:
        client.close()

    assert raised.value.status_code == status_code
    assert "secret-token" not in str(raised.value)
    assert raised.value.correlation_id == "request-123"
