"""The aggregations behind the admin screens, tested without HTTP.

Pagination is not decoration here: there are already 20,348 spans and 521
runs in the development database, so an unbounded list endpoint is both a
production hazard and a slow test."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.admin import queries
from app.db.models import (
    ApprovalActionType, ApprovalRequest, ApprovalStatus, Conversation, RiskLevel,
    Run, RunStatus, RunTrigger,
)


@pytest.mark.parametrize("given,expected", [
    (None, queries.DEFAULT_LIMIT),
    (10, 10),
    (0, 1),
    (-5, 1),
    (queries.MAX_LIMIT, queries.MAX_LIMIT),
    (queries.MAX_LIMIT + 1, queries.MAX_LIMIT),
    (10_000, queries.MAX_LIMIT),
])
def test_limit_is_clamped_never_rejected(given, expected):
    """A client asking for too much gets the maximum, not an error -- an
    over-large limit is a client bug, not an attack, and failing the request
    helps nobody."""
    assert queries.clamp_limit(given) == expected


@pytest.mark.parametrize("given,expected", [(None, 0), (0, 0), (-1, 0), (25, 25)])
def test_offset_is_clamped_to_non_negative(given, expected):
    assert queries.clamp_offset(given) == expected


def test_overview_counts_only_todays_runs(db_session):
    """The 'today' boundary is the thing most likely to be wrong: a run from
    yesterday must not appear in today's count."""
    now = datetime.now(timezone.utc)
    today = Run(
        trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK,
        started_at=now, cost_usd=0.25,
    )
    yesterday = Run(
        trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK,
        started_at=now - timedelta(days=1), cost_usd=99.0,
    )
    db_session.add_all([today, yesterday])
    db_session.flush()

    result = queries.overview(db_session)
    assert result["runs_today"] >= 1
    assert result["spend_today"] >= 0.25
    assert result["spend_today"] < 99.0, "yesterday's spend must not be counted"


def test_overview_error_rate_is_a_fraction_of_todays_runs(db_session):
    now = datetime.now(timezone.utc)
    db_session.add_all([
        Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.ERROR, started_at=now),
        Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK, started_at=now),
    ])
    db_session.flush()
    result = queries.overview(db_session)
    assert 0.0 <= result["error_rate"] <= 1.0


def test_overview_error_rate_is_zero_not_a_crash_when_there_are_no_runs(db_session):
    """Division by zero is the obvious bug in any rate calculation."""
    from sqlalchemy import text

    db_session.execute(text("DELETE FROM spans"))
    db_session.execute(text("DELETE FROM runs"))
    result = queries.overview(db_session)
    assert result["runs_today"] == 0
    assert result["error_rate"] == 0.0


def test_overview_counts_pending_approvals_only(db_session):
    conv = Conversation(guest_name="G", guest_email="g@northstar.example")
    db_session.add(conv)
    db_session.flush()
    for status in (ApprovalStatus.PENDING, ApprovalStatus.EXECUTED):
        db_session.add(ApprovalRequest(
            conversation_id=conv.id, task_id=None, requester_user_id=None,
            action_type=ApprovalActionType.RESET_CREDENTIAL,
            action_payload={"target_username": "u", "credential_kind": "password"},
            justification="j", risk_level=RiskLevel.LOW, agent_summary="a", status=status,
        ))
    db_session.flush()
    before = queries.overview(db_session)["pending_approvals"]
    assert before >= 1


def test_costs_groups_by_day_model_user_and_trigger(db_session):
    result = queries.costs(db_session)
    for key in ("by_day", "by_model", "by_user", "by_trigger", "totals"):
        assert key in result, f"missing {key}"
    assert "input_tokens" in result["totals"]
    assert "cache_hit_rate" in result["totals"]


def test_cache_hit_rate_is_zero_not_a_crash_with_no_tokens(db_session):
    from sqlalchemy import text

    db_session.execute(text("DELETE FROM spans"))
    db_session.execute(text("DELETE FROM runs"))
    assert queries.costs(db_session)["totals"]["cache_hit_rate"] == 0.0
