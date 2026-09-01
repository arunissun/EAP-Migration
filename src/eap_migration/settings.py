"""Environment-backed settings and immutable API safety boundaries."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .exceptions import ConfigurationError, SafetyError

STAGE_ORIGIN = "https://goadmin-stage.ifrc.org"
STAGE_API_BASE_URL = f"{STAGE_ORIGIN}/api/v2"
API_MAX_FILE_SIZE = 100 * 1024 * 1024


class Environment(StrEnum):
    STAGE = "stage"


class Settings(BaseSettings):
    """Settings loaded from the process environment and an optional root .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    api_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GO_EAP_API_TOKEN", "api_token"),
    )
    contact_email: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GO_EAP_CONTACT_EMAIL", "contact_email"),
    )
    contact_phone: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GO_EAP_CONTACT_PHONE", "contact_phone"),
    )
    timeout_seconds: float = Field(
        default=30.0,
        validation_alias=AliasChoices("GO_EAP_TIMEOUT_SECONDS", "timeout_seconds"),
    )
    get_retries: int = Field(
        default=3,
        validation_alias=AliasChoices("GO_EAP_GET_RETRIES", "get_retries"),
    )
    max_file_size_bytes: int = Field(
        default=API_MAX_FILE_SIZE,
        validation_alias=AliasChoices("GO_EAP_MAX_FILE_SIZE_BYTES", "max_file_size_bytes"),
    )

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: float) -> float:
        if value <= 0 or value > 300:
            raise ValueError("timeout_seconds must be between 0 and 300")
        return value

    @field_validator("get_retries")
    @classmethod
    def validate_retries(cls, value: int) -> int:
        if value < 0 or value > 8:
            raise ValueError("get_retries must be between 0 and 8")
        return value

    @field_validator("max_file_size_bytes")
    @classmethod
    def validate_max_file_size(cls, value: int) -> int:
        if value <= 0 or value > API_MAX_FILE_SIZE:
            raise ValueError(
                f"max_file_size_bytes must be between 1 and {API_MAX_FILE_SIZE} bytes"
            )
        return value

    def token_value(self) -> str:
        if self.api_token is None or not self.api_token.get_secret_value().strip():
            raise ConfigurationError(
                "GO_EAP_API_TOKEN is missing. Add it to the repository .env file "
                "or set it in the current PowerShell session."
            )
        return self.api_token.get_secret_value().strip()

    def contact_environment(self) -> dict[str, str]:
        environment: dict[str, str] = {}
        if self.contact_email and self.contact_email.strip():
            environment["GO_EAP_CONTACT_EMAIL"] = self.contact_email.strip()
        if self.contact_phone and self.contact_phone.strip():
            environment["GO_EAP_CONTACT_PHONE"] = self.contact_phone.strip()
        return environment


def api_base_url(environment: Environment | Literal["stage"] = Environment.STAGE) -> str:
    """Return the only write-capable API base URL supported in version one."""

    if Environment(environment) is not Environment.STAGE:
        raise SafetyError(f"Unsupported environment: {environment}")
    return STAGE_API_BASE_URL


def enforce_stage_origin(url: str) -> None:
    """Reject URLs outside the exact staging origin, including lookalike hosts."""

    parsed = urlparse(url)
    expected = urlparse(STAGE_ORIGIN)
    if (parsed.scheme, parsed.hostname, parsed.port) != (
        expected.scheme,
        expected.hostname,
        expected.port,
    ):
        raise SafetyError(
            f"State-changing EAP API access is blocked for {url}. "
            f"The only allowed origin is {STAGE_ORIGIN}."
        )
