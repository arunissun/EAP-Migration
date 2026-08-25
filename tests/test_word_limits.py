import pytest

from eap_migration.exceptions import ValidationFailure
from eap_migration.word_limits import validate_narrative_limits, word_count


def test_word_count_uses_non_whitespace_tokens() -> None:
    assert word_count("  one   two\nthree\tfour  ") == 4


def test_simplified_exact_word_limit_passes() -> None:
    validate_narrative_limits(
        {"trigger_statement": " ".join(["word"] * 100)},
        "simplified",
    )


def test_simplified_over_limit_fails_without_truncation() -> None:
    with pytest.raises(ValidationFailure, match="trigger_statement.*maximum is 100"):
        validate_narrative_limits(
            {"trigger_statement": " ".join(["word"] * 101)},
            "simplified",
        )


def test_full_nested_narrative_limit_is_checked() -> None:
    with pytest.raises(ValidationFailure, match="key_actors.0.description"):
        validate_narrative_limits(
            {"key_actors": [{"description": " ".join(["word"] * 151)}]},
            "full",
        )
