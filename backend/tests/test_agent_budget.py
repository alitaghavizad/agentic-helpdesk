from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.agent.budget import AbortRun, TurnBudget, agent_enabled, check_and_record_usage, new_turn_budget
from app.config import get_settings
from app.db.models import UsageCounter
from app.db.session import get_sessionmaker


@pytest.fixture()
def cleanup_usage_counter():
    def _cleanup(user_key: str) -> None:
        Session = get_sessionmaker()
        with Session() as session:
            session.query(UsageCounter).filter(UsageCounter.user_key == user_key).delete()
            session.commit()

    return _cleanup


def test_turn_budget_raises_after_max_iterations():
    budget = TurnBudget(max_iterations=2, max_cost_usd=Decimal("10"), max_wall_seconds=60)
    budget.record_iteration(None)
    budget.check()
    budget.record_iteration(None)
    with pytest.raises(AbortRun, match="2 tool iterations"):
        budget.check()


def test_turn_budget_raises_after_cost_cap():
    budget = TurnBudget(max_iterations=100, max_cost_usd=Decimal("0.10"), max_wall_seconds=60)
    budget.record_iteration(Decimal("0.06"))
    budget.check()
    budget.record_iteration(Decimal("0.06"))
    with pytest.raises(AbortRun, match=r"\$0.10"):
        budget.check()


def test_turn_budget_raises_after_wall_clock():
    budget = TurnBudget(max_iterations=100, max_cost_usd=Decimal("10"), max_wall_seconds=0.05)
    time.sleep(0.1)
    with pytest.raises(AbortRun, match="0.05s wall clock"):
        budget.check()


def test_turn_budget_unpriced_iteration_does_not_raise_on_cost():
    budget = TurnBudget(max_iterations=100, max_cost_usd=Decimal("0.10"), max_wall_seconds=60)
    budget.record_iteration(None)
    budget.check()  # no error -- None contributes 0 to cost


def test_new_turn_budget_reads_settings_defaults():
    budget = new_turn_budget()
    settings = get_settings()
    assert budget.max_iterations == settings.max_tool_iterations
    assert budget.max_cost_usd == Decimal(str(settings.max_cost_per_conversation_usd))
    assert budget.max_wall_seconds == 60


def test_agent_enabled_reflects_settings():
    assert agent_enabled() == get_settings().agent_enabled


def test_check_and_record_usage_allows_under_the_cap(cleanup_usage_counter):
    user_key = "test-user-budget-1"
    try:
        check_and_record_usage(user_key, tokens=1000, cost=Decimal("0.01"))
        check_and_record_usage(user_key, tokens=1000, cost=Decimal("0.01"))
    finally:
        cleanup_usage_counter(user_key)


def test_check_and_record_usage_raises_over_hourly_request_cap(cleanup_usage_counter):
    user_key = "test-user-budget-2"
    try:
        for _ in range(30):
            check_and_record_usage(user_key, tokens=10, cost=Decimal("0.001"))
        with pytest.raises(AbortRun, match="30 requests"):
            check_and_record_usage(user_key, tokens=10, cost=Decimal("0.001"))
    finally:
        cleanup_usage_counter(user_key)


def test_check_and_record_usage_raises_over_daily_token_cap(cleanup_usage_counter):
    user_key = "test-user-budget-3"
    try:
        check_and_record_usage(user_key, tokens=200_000, cost=Decimal("1"))
        with pytest.raises(AbortRun, match="200000 tokens"):
            check_and_record_usage(user_key, tokens=1, cost=Decimal("0.001"))
    finally:
        cleanup_usage_counter(user_key)


def test_check_and_record_usage_daily_cap_sums_across_hourly_buckets(cleanup_usage_counter):
    """The daily cap must sum tokens across every hourly UsageCounter row
    for this user in the last 24h, not just the current hour's row --
    otherwise a user could reset their daily allowance every 60 minutes."""
    user_key = "test-user-budget-4"
    Session = get_sessionmaker()
    try:
        now = datetime.now(timezone.utc)
        with Session() as session:
            # Simulate two prior hours already having consumed 150k tokens total.
            session.add(UsageCounter(
                user_key=user_key, window_start=now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=2),
                requests=1, input_tokens=100_000, output_tokens=0, cost_usd=Decimal("1"),
            ))
            session.add(UsageCounter(
                user_key=user_key, window_start=now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1),
                requests=1, input_tokens=50_000, output_tokens=0, cost_usd=Decimal("1"),
            ))
            session.commit()

        with pytest.raises(AbortRun, match="200000 tokens"):
            check_and_record_usage(user_key, tokens=50_001, cost=Decimal("0.001"))
    finally:
        cleanup_usage_counter(user_key)
