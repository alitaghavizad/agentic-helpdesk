from app.tracing.redaction import REDACTED, redact


def test_redact_scrubs_password_named_field_regardless_of_value():
    result = redact({"password": "hunter2", "username": "alice"})
    assert result == {"password": REDACTED, "username": "alice"}


def test_redact_scrubs_secret_and_token_and_api_key_named_fields():
    result = redact({"secret": "x", "token": "y", "api_key": "z", "apiKey": "w"})
    assert result == {"secret": REDACTED, "token": REDACTED, "api_key": REDACTED, "apiKey": REDACTED}


def test_redact_scrubs_api_key_shaped_string_in_free_text():
    text = "Here is your key: sk-ABCDEFGHIJ1234567890, use it wisely."
    result = redact({"output": text})
    assert "sk-ABCDEFGHIJ1234567890" not in result["output"]
    assert REDACTED in result["output"]


def test_redact_scrubs_bearer_token():
    result = redact("Authorization: Bearer abc123.def456-ghi789xyz")
    assert "abc123.def456-ghi789xyz" not in result
    assert f"Bearer {REDACTED}" in result


def test_redact_scrubs_long_digit_sequences():
    result = redact({"note": "Card on file: 4111111111111111, thanks."})
    assert "4111111111111111" not in result["note"]
    assert REDACTED in result["note"]


def test_redact_preserves_non_secret_structure_unchanged():
    original = {"tool": "search_knowledge", "args": {"query": "vpn setup", "k": 5}}
    assert redact(original) == original


def test_redact_recurses_into_lists_and_nested_dicts():
    original = {
        "results": [
            {"secret": "sk-verysecretkey1234567890"},
            {"topic": "vpn", "count": 3},
        ]
    }
    result = redact(original)
    assert result["results"][0] == {"secret": REDACTED}
    assert result["results"][1] == {"topic": "vpn", "count": 3}


def test_redact_leaves_short_numbers_and_non_string_values_alone():
    original = {"k": 5, "score": 0.87, "ok": True, "port": 8080}
    assert redact(original) == original


def test_redact_does_not_match_token_substrings_in_telemetry_field_names():
    """Ensure 'token' substring matches don't redact API usage telemetry fields."""
    original = {
        "input_tokens": 42,
        "output_tokens": 100,
        "cache_read_tokens": 5,
        "cache_write_tokens": 10,
    }
    # All token-count fields should pass through unchanged
    assert redact(original) == original


def test_redact_does_not_match_pass_substring_in_passed_field():
    """Ensure 'pass' substring doesn't redact the 'passed' verb field."""
    original = {"passed": True, "eval_gate": "retrieval"}
    # Both fields should pass through unchanged
    assert redact(original) == original


def test_redact_still_catches_compound_secret_field_names():
    """Ensure compound names like db_password and auth_token are still redacted."""
    original = {
        "db_password": "super_secret_db_pw",
        "auth_token": "Bearer xyz123",
        "user_secret": "shh",
    }
    expected = {
        "db_password": REDACTED,
        "auth_token": REDACTED,
        "user_secret": REDACTED,
    }
    assert redact(original) == expected


def test_redact_catches_camelcase_secret_field_names():
    """A lookaround-based word-boundary regex under IGNORECASE fails to see
    the boundary between a lowercase letter and the uppercase start of a
    camelCase hump (e.g. authToken), so camelCase secret field names were
    silently passing through unredacted. Word-splitting must catch them."""
    original = {
        "authToken": "abc",
        "dbPassword": "def",
        "userSecret": "ghi",
        "passwordHash": "jkl",
        "accessToken": "mno",
        "clientSecret": "pqr",
        "sessionToken": "stu",
    }
    expected = {key: REDACTED for key in original}
    assert redact(original) == expected


def test_redact_does_not_match_camelcase_telemetry_field_names():
    """camelCase telemetry names must not false-positive the way their
    snake_case counterparts (input_tokens etc.) don't."""
    original = {"inputTokens": 42, "outputTokens": 100}
    assert redact(original) == original
