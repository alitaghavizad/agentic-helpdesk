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
