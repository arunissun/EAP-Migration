import pytest

from eap_migration.exceptions import ConfigurationError, SafetyError
from eap_migration.logging import redact
from eap_migration.settings import enforce_stage_origin


def test_stage_guard_rejects_lookalike_host() -> None:
    with pytest.raises(SafetyError):
        enforce_stage_origin("https://goadmin-stage.ifrc.org.attacker.example/api/v2")


def test_stage_guard_accepts_exact_origin() -> None:
    enforce_stage_origin("https://goadmin-stage.ifrc.org/api/v2")


def test_redaction_removes_secret_fields_and_values() -> None:
    value = redact(
        {"Authorization": "Token abc123", "nested": "abc123", "safe": "value"},
        ("abc123",),
    )
    assert value == {"Authorization": "[REDACTED]", "nested": "[REDACTED]", "safe": "value"}


def test_missing_token_is_an_actionable_configuration_error(monkeypatch) -> None:
    monkeypatch.delenv("GO_EAP_API_TOKEN", raising=False)
    from eap_migration.settings import Settings

    settings = Settings()
    settings.api_token = None
    with pytest.raises(ConfigurationError, match="GO_EAP_API_TOKEN"):
        settings.token_value()
