"""Authenticated HTTP client with GET-only retries and safe error handling."""

from __future__ import annotations

import mimetypes
import time
from collections.abc import Mapping
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .exceptions import ApiError, EapMigrationError, RecoveryRequired
from .logging import redact
from .settings import STAGE_API_BASE_URL, enforce_stage_origin


class EapApiClient:
    """Small API client; no method exists for lifecycle or delete endpoints."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = STAGE_API_BASE_URL,
        timeout_seconds: float = 30.0,
        get_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not token.strip():
            raise ValueError("token cannot be empty")
        parsed = urlparse(base_url.rstrip("/"))
        enforce_stage_origin(f"{parsed.scheme}://{parsed.netloc}")
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._get_retries = get_retries
        self.last_correlation_id: str | None = None
        self._client = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )

    def __enter__(self) -> EapApiClient:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            url = path_or_url
        else:
            url = f"{self.base_url}/{path_or_url.lstrip('/')}"
        enforce_stage_origin(url)
        return url

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self._token}",
            "Accept": "application/json",
        }

    def _response_body(self, response: httpx.Response) -> Any:
        try:
            return redact(response.json(), (self._token,))
        except ValueError:
            return redact(response.text[:4000], (self._token,))

    def redact_for_output(self, value: Any) -> Any:
        """Redact this client's credential before a value is printed or saved."""

        return redact(value, (self._token,))

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        correlation_id = next(
            (
                response.headers.get(name)
                for name in ("X-Request-ID", "X-Request-Id", "X-Correlation-ID")
                if response.headers.get(name)
            ),
            None,
        )
        raise ApiError(
            method=response.request.method,
            url=str(response.request.url),
            status_code=response.status_code,
            response_body=self._response_body(response),
            correlation_id=correlation_id,
        )

    @staticmethod
    def _response_correlation_id(response: httpx.Response) -> str | None:
        return next(
            (
                response.headers.get(name)
                for name in ("X-Request-ID", "X-Request-Id", "X-Correlation-ID")
                if response.headers.get(name)
            ),
            None,
        )

    def _request(self, method: str, path_or_url: str, **kwargs: Any) -> httpx.Response:
        url = self._url(path_or_url)
        headers = dict(self._headers())
        headers.update(kwargs.pop("headers", {}))
        try:
            response = self._client.request(method, url, headers=headers, **kwargs)
            self.last_correlation_id = self._response_correlation_id(response)
            return response
        except httpx.RequestError as exc:
            if method.upper() == "GET":
                raise
            raise RecoveryRequired(
                f"{method.upper()} outcome is ambiguous for {url}; inspect staging before retrying"
            ) from exc

    @staticmethod
    def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                value = float(retry_after)
                if value >= 0:
                    return min(value, 2.0)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.astimezone()
                    seconds = retry_at.timestamp() - time.time()
                    if seconds >= 0:
                        return min(seconds, 2.0)
                except (TypeError, ValueError, OverflowError):
                    pass
        return min(0.25 * (2**attempt), 2.0)

    def get(self, path_or_url: str, *, params: Mapping[str, Any] | None = None) -> Any:
        last_error: EapMigrationError | None = None
        for attempt in range(self._get_retries + 1):
            try:
                response = self._request("GET", path_or_url, params=params)
            except httpx.RequestError as exc:
                if attempt >= self._get_retries:
                    raise EapMigrationError(
                        f"GET request failed for {self._url(path_or_url)} after "
                        f"{attempt + 1} attempt(s): {exc}"
                    ) from exc
                time.sleep(min(0.25 * (2**attempt), 2.0))
                continue
            if (
                response.status_code not in {429, 500, 502, 503, 504}
                or attempt >= self._get_retries
            ):
                self._raise_for_status(response)
                try:
                    return response.json()
                except ValueError as exc:
                    raise EapMigrationError(
                        f"GET {response.url} returned non-JSON content"
                    ) from exc
            last_error = ApiError(
                method="GET",
                url=str(response.request.url),
                status_code=response.status_code,
                response_body=self._response_body(response),
            )
            time.sleep(self._retry_after_seconds(response, attempt))
        raise last_error or EapMigrationError("GET retry loop ended unexpectedly")

    def post_json(self, path_or_url: str, payload: dict[str, Any]) -> Any:
        response = self._request("POST", path_or_url, json=payload)
        self._raise_for_status(response)
        try:
            return response.json()
        except ValueError as exc:
            raise EapMigrationError(f"POST {response.url} returned non-JSON content") from exc

    def patch_json(self, path_or_url: str, payload: dict[str, Any]) -> Any:
        response = self._request("PATCH", path_or_url, json=payload)
        self._raise_for_status(response)
        try:
            return response.json()
        except ValueError as exc:
            raise EapMigrationError(f"PATCH {response.url} returned non-JSON content") from exc

    def upload_file(self, path: Path, *, caption: str | None = None) -> Any:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            with path.open("rb") as handle:
                response = self._request(
                    "POST",
                    "/eap-file/",
                    files={"file": (path.name, handle, content_type)},
                    data={"caption": caption} if caption is not None else None,
                )
        except OSError as exc:
            raise EapMigrationError(f"Could not read upload file {path}: {exc}") from exc
        self._raise_for_status(response)
        try:
            return response.json()
        except ValueError as exc:
            raise EapMigrationError(f"POST {response.url} returned non-JSON content") from exc

    def get_paginated(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        next_url: str | None = path_or_url
        next_params = params
        rows: list[dict[str, Any]] = []
        count: int | None = None
        while next_url:
            body = self.get(next_url, params=next_params)
            if not isinstance(body, dict) or not isinstance(body.get("results"), list):
                raise EapMigrationError(f"Expected a paginated response from {next_url}")
            if count is None and isinstance(body.get("count"), int):
                count = body["count"]
            rows.extend(row for row in body["results"] if isinstance(row, dict))
            candidate = body.get("next")
            next_url = candidate if isinstance(candidate, str) and candidate else None
            next_params = None
        return rows, count

    def check_authentication(self) -> dict[str, Any]:
        body = self.get("/eap/options/")
        if not isinstance(body, dict):
            raise EapMigrationError("The authentication check returned an unexpected response")
        return body
