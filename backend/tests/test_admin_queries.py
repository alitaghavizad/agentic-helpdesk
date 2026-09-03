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
    Run, RunStatus, RunTrigger, Span, SpanKind, SpanStatus,
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


def test_error_rate_excludes_in_flight_runs_from_its_denominator(db_session):
    """A run that has not finished cannot be an error yet, so counting
    RUNNING rows in the denominator drags the rate down exactly when a burst
    of traffic is in flight -- the moment the number matters most. Here one
    error and one OK run make a true rate of 0.5; the eight in-flight runs
    would have reported 0.1."""
    from sqlalchemy import text

    db_session.execute(text("DELETE FROM spans"))
    db_session.execute(text("DELETE FROM runs"))
    now = datetime.now(timezone.utc)
    db_session.add_all([
        Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.ERROR, started_at=now),
        Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK, started_at=now),
        *[
            Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.RUNNING, started_at=now)
            for _ in range(8)
        ],
    ])
    db_session.flush()

    result = queries.overview(db_session)
    # runs_today answers "how much happened today", which includes work in
    # progress -- excluding RUNNING from the RATE must not shrink the COUNT.
    assert result["runs_today"] == 10
    assert result["error_rate"] == pytest.approx(0.5)


def test_error_rate_counts_aborted_as_a_completed_non_error(db_session):
    """ABORTED is a budget cutoff (app/agent/budget.py), not a failure: it is
    a finished outcome, so it belongs in the denominator and out of the
    numerator. One error and three aborted runs is a rate of 0.25."""
    from sqlalchemy import text

    db_session.execute(text("DELETE FROM spans"))
    db_session.execute(text("DELETE FROM runs"))
    now = datetime.now(timezone.utc)
    db_session.add_all([
        Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.ERROR, started_at=now),
        *[
            Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.ABORTED, started_at=now)
            for _ in range(3)
        ],
    ])
    db_session.flush()

    result = queries.overview(db_session)
    assert result["error_rate"] == pytest.approx(0.25)


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


def test_cache_hit_rate_counts_cache_writes_in_its_denominator(db_session):
    """A run that WRITES 900 tokens of cache and reads 100 has a 10% hit
    rate, not 100%. Excluding writes made cache-establishing workloads look
    perfectly cached."""
    from sqlalchemy import text

    db_session.execute(text("DELETE FROM spans"))
    db_session.execute(text("DELETE FROM runs"))
    db_session.add(Run(
        trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK,
        started_at=datetime.now(timezone.utc),
        input_tokens=0, output_tokens=50,
        cache_read_tokens=100, cache_write_tokens=900,
    ))
    db_session.flush()

    totals = queries.costs(db_session)["totals"]
    assert totals["input_tokens"] == 0
    assert totals["cache_read_tokens"] == 100
    assert totals["cache_write_tokens"] == 900

    # Derived from the totals the function itself reports, so the assertion
    # survives any row that leaks into the aggregate rather than silently
    # becoming vacuous.
    expected = totals["cache_read_tokens"] / (
        totals["input_tokens"] + totals["cache_read_tokens"] + totals["cache_write_tokens"]
    )
    assert totals["cache_hit_rate"] == pytest.approx(expected)
    assert totals["cache_hit_rate"] == pytest.approx(0.1)
    # The old denominator (input + reads only) reported a flat 1.0 here; no
    # correct denominator that includes the 900 written tokens can reach 0.5.
    assert totals["cache_hit_rate"] < 0.5


def test_cache_hit_rate_is_zero_not_a_crash_with_no_tokens(db_session):
    from sqlalchemy import text

    db_session.execute(text("DELETE FROM spans"))
    db_session.execute(text("DELETE FROM runs"))
    assert queries.costs(db_session)["totals"]["cache_hit_rate"] == 0.0


def _make_span(run_id, sequence, *, model, cost_usd):
    """A minimal LLM-kind span for the unpriced_calls tests below. Only the
    columns queries.costs() actually reads are given real values; the rest
    take Span's own defaults/nullability."""
    return Span(
        run_id=run_id, sequence=sequence, kind=SpanKind.LLM, name="llm_call",
        status=SpanStatus.OK, model=model, cost_usd=cost_usd,
    )


def test_by_model_reports_unpriced_calls_separately_from_the_coalesced_zero(db_session):
    """queries.costs()'s by_model coalesces an all-NULL group's SUM to 0 --
    a SUM cannot represent "unknown" -- so a model with two genuinely-unpriced
    calls must still show cost_usd == 0.0 (unchanged), but unpriced_calls == 2
    is what actually says those calls were unpriced rather than free. A
    sibling model with one priced call must report unpriced_calls == 0."""
    from sqlalchemy import text

    db_session.execute(text("DELETE FROM spans"))
    db_session.execute(text("DELETE FROM runs"))
    run = Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK, started_at=datetime.now(timezone.utc))
    db_session.add(run)
    db_session.flush()
    db_session.add_all([
        _make_span(run.id, 0, model="priced-model", cost_usd=0.5),
        _make_span(run.id, 1, model="unpriced-model", cost_usd=None),
        _make_span(run.id, 2, model="unpriced-model", cost_usd=None),
    ])
    db_session.flush()

    by_model = {row["model"]: row for row in queries.costs(db_session)["by_model"]}

    assert by_model["priced-model"]["cost_usd"] == pytest.approx(0.5)
    assert by_model["priced-model"]["calls"] == 1
    assert by_model["priced-model"]["unpriced_calls"] == 0

    assert by_model["unpriced-model"]["cost_usd"] == 0.0
    assert by_model["unpriced-model"]["calls"] == 2
    assert by_model["unpriced-model"]["unpriced_calls"] == 2


def test_totals_unpriced_calls_counts_every_model_with_no_model_less_llm_spans_present(db_session):
    """When every unpriced LLM span DOES carry a model, totals.unpriced_calls
    and by_model's own per-row counts happen to agree -- but, per
    test_totals_counts_an_errored_llm_call_with_no_model_as_unpriced below,
    that agreement is not something totals is computed FROM any more (it
    used to be: summing by_model's rows silently dropped a model-less LLM
    span, which is exactly the case this feature exists to catch). This
    pins the ordinary case; the test below pins the one that broke it."""
    from sqlalchemy import text

    db_session.execute(text("DELETE FROM spans"))
    db_session.execute(text("DELETE FROM runs"))
    run = Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK, started_at=datetime.now(timezone.utc))
    db_session.add(run)
    db_session.flush()
    db_session.add_all([
        _make_span(run.id, 0, model="model-a", cost_usd=None),
        _make_span(run.id, 1, model="model-b", cost_usd=None),
        _make_span(run.id, 2, model="model-b", cost_usd=1.0),
    ])
    db_session.flush()

    result = queries.costs(db_session)
    assert result["totals"]["unpriced_calls"] == 2
    assert result["totals"]["unpriced_calls"] == sum(
        row["unpriced_calls"] for row in result["by_model"]
    )


def test_totals_counts_an_errored_llm_call_with_no_model_as_unpriced(db_session):
    """app/agent/loop.py opens `span(SpanKind.LLM, ...)` BEFORE calling the
    model (loop.py:96-108); if that call raises, `recorder.record_usage(...)`
    -- which is what sets both `model` and `cost_usd` -- never runs, and the
    span persists as kind=LLM, model=NULL, cost_usd=NULL. That is a real LLM
    call whose cost is genuinely unknown: exactly what unpriced_calls exists
    to surface.

    by_model's own grouping filters `Span.model.isnot(None)` and so cannot
    see this span at all -- it has no model to group by. Computing
    totals.unpriced_calls by summing by_model's rows (the earlier
    implementation) therefore silently dropped this exact case; the
    assertion below on `by_model` is what proves the gap, not just the
    total. totals.unpriced_calls must come from its own query, keyed on
    `kind == LLM`, never on `model`."""
    from sqlalchemy import text

    db_session.execute(text("DELETE FROM spans"))
    db_session.execute(text("DELETE FROM runs"))
    run = Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.ERROR, started_at=datetime.now(timezone.utc))
    db_session.add(run)
    db_session.flush()
    db_session.add(Span(
        run_id=run.id, sequence=0, kind=SpanKind.LLM, name="beta.messages.stream",
        status=SpanStatus.ERROR, model=None, cost_usd=None,
    ))
    db_session.flush()

    result = queries.costs(db_session)
    assert result["totals"]["unpriced_calls"] == 1
    # The gap this test exists to catch: by_model cannot see this span at
    # all (it has no model to group by), so summing its rows would report 0
    # here while the true count is 1.
    assert sum(row["unpriced_calls"] for row in result["by_model"]) == 0


def test_a_span_with_no_model_is_not_counted_as_an_unpriced_call(db_session):
    """A tool-call span has no model and so was never eligible for pricing
    in the first place (app/tracing/spans.py only calls cost_for when
    `self.recorder.model is not None`) -- it is not "unpriced" in the
    informative sense unpriced_calls exists to flag, it is simply not an
    LLM call. by_model already excludes these via `Span.model.isnot(None)`;
    this pins that the new count respects the same boundary."""
    from sqlalchemy import text

    db_session.execute(text("DELETE FROM spans"))
    db_session.execute(text("DELETE FROM runs"))
    run = Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK, started_at=datetime.now(timezone.utc))
    db_session.add(run)
    db_session.flush()
    db_session.add(Span(
        run_id=run.id, sequence=0, kind=SpanKind.TOOL, name="some_tool",
        status=SpanStatus.OK, model=None, cost_usd=None,
    ))
    db_session.flush()

    result = queries.costs(db_session)
    assert result["totals"]["unpriced_calls"] == 0
    assert result["by_model"] == []
