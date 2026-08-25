from eap_migration.logging import configure_logging


def test_structured_events_redact_secret_values(capsys) -> None:
    logger = configure_logging(("secret-token",))
    logger.info(
        "checkpoint",
        extra={"event_data": {"checkpoint": "application_post", "token": "secret-token"}},
    )

    output = capsys.readouterr().err
    assert "checkpoint" in output
    assert "secret-token" not in output
    assert "[REDACTED]" in output
