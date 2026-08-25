from decimal import Decimal

from app.config import get_settings
from app.tracing import pricing


def test_cost_for_known_model_input_and_output_only():
    cost = pricing.cost_for("claude-opus-5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == Decimal("30.000000")  # $5.00 input + $25.00 output per spec 17


def test_cost_for_includes_cache_write_and_read_multipliers():
    cost = pricing.cost_for(
        "claude-opus-5",
        cache_write_tokens=1_000_000,  # 5.00 * 1.25 = 6.25
        cache_read_tokens=1_000_000,   # 5.00 * 0.1  = 0.50
    )
    assert cost == Decimal("6.750000")


def test_cost_for_zero_usage_is_zero_not_none():
    assert pricing.cost_for("claude-opus-5") == Decimal("0.000000")


def test_cost_for_unknown_model_returns_none_not_a_fabricated_number():
    assert pricing.cost_for("some-future-unreleased-model", input_tokens=1000) is None


def test_cost_for_unconfigured_gemini_model_is_unpriced_by_default():
    # No hardcoded Gemini rate exists (see plan's Global Constraints) --
    # an unpriced model must render as unpriced, not a guessed number.
    assert pricing.cost_for(get_settings().gemini_model, input_tokens=1000) is None


def test_model_pricing_overrides_env_var_changes_an_existing_rate(monkeypatch):
    monkeypatch.setenv("MODEL_PRICING_OVERRIDES", '{"claude-opus-5": {"input": 1.0, "output": 2.0}}')
    get_settings.cache_clear()
    try:
        cost = pricing.cost_for("claude-opus-5", input_tokens=1_000_000, output_tokens=1_000_000)
        assert cost == Decimal("3.000000")
    finally:
        monkeypatch.delenv("MODEL_PRICING_OVERRIDES", raising=False)
        get_settings.cache_clear()


def test_model_pricing_overrides_malformed_json_falls_back_to_defaults(monkeypatch, capsys):
    """cost_for's docstring promises it never raises. A malformed
    MODEL_PRICING_OVERRIDES value must not violate that -- it should be
    ignored (with a stderr warning), falling back to DEFAULT_RATES."""
    monkeypatch.setenv("MODEL_PRICING_OVERRIDES", "{not valid json")
    get_settings.cache_clear()
    try:
        cost = pricing.cost_for("claude-opus-5", input_tokens=1_000_000, output_tokens=1_000_000)
        assert cost == Decimal("30.000000")  # unaffected default rate
        assert "malformed" in capsys.readouterr().err.lower()
    finally:
        monkeypatch.delenv("MODEL_PRICING_OVERRIDES", raising=False)
        get_settings.cache_clear()


def test_model_pricing_overrides_incomplete_entry_falls_back_to_defaults(monkeypatch):
    """A well-formed JSON override missing the 'output' key must not raise
    KeyError -- it should be ignored, same as fully malformed JSON."""
    monkeypatch.setenv("MODEL_PRICING_OVERRIDES", '{"claude-opus-5": {"input": 1.0}}')
    get_settings.cache_clear()
    try:
        cost = pricing.cost_for("claude-opus-5", input_tokens=1_000_000, output_tokens=1_000_000)
        assert cost == Decimal("30.000000")  # unaffected default rate
    finally:
        monkeypatch.delenv("MODEL_PRICING_OVERRIDES", raising=False)
        get_settings.cache_clear()


def test_model_pricing_overrides_env_var_can_add_a_new_model(monkeypatch):
    monkeypatch.setenv(
        "MODEL_PRICING_OVERRIDES", '{"gemini-2.5-flash": {"input": 0.3, "output": 2.5}}'
    )
    get_settings.cache_clear()
    try:
        cost = pricing.cost_for("gemini-2.5-flash", input_tokens=1_000_000, output_tokens=1_000_000)
        assert cost == Decimal("2.800000")
    finally:
        monkeypatch.delenv("MODEL_PRICING_OVERRIDES", raising=False)
        get_settings.cache_clear()
