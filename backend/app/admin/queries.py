"""Read aggregations behind the admin screens (spec 15).

Pure query functions over a Session: no HTTP, no serialisation. That split
is what lets nine screens' worth of aggregation be tested directly instead
of through nine endpoints, and it keeps the router small enough to review.

Every list is paginated with a hard server-side cap. There are already
20,348 spans and 521 runs in development, so an unbounded list endpoint is
a production hazard, not a theoretical one.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, NamedTuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    ApprovalRequest, ApprovalStatus, Run, RunStatus, Span, Ticket, TicketStatus, User,
)

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


class Page(NamedTuple):
    items: list
    total: int
    limit: int
    offset: int


def clamp_limit(limit: int | None) -> int:
    """Clamps rather than rejects. An over-large limit is a client bug, not
    an attack; returning the maximum is more useful than a 422, and the cap
    is what actually protects the database."""
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), MAX_LIMIT))


def clamp_offset(offset: int | None) -> int:
    if offset is None:
        return 0
    return max(0, int(offset))


def _start_of_today() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def overview(db: Session) -> dict[str, Any]:
    """Spec 15's Overview screen. Every counter is scoped to today except
    the queues (pending approvals, open tickets), which are backlogs and are
    meaningless scoped to a day."""
    since = _start_of_today()

    runs_today = db.query(func.count(Run.id)).filter(Run.started_at >= since).scalar() or 0
    errors_today = db.query(func.count(Run.id)).filter(
        Run.started_at >= since, Run.status == RunStatus.ERROR,
    ).scalar() or 0
    spend_today = db.query(func.coalesce(func.sum(Run.cost_usd), 0)).filter(
        Run.started_at >= since,
    ).scalar() or 0

    pending_approvals = db.query(func.count(ApprovalRequest.id)).filter(
        ApprovalRequest.status == ApprovalStatus.PENDING,
    ).scalar() or 0
    open_tickets = db.query(func.count(Ticket.id)).filter(
        Ticket.status.in_([TicketStatus.OPEN, TicketStatus.ASSIGNED, TicketStatus.IN_PROGRESS]),
    ).scalar() or 0

    return {
        "runs_today": int(runs_today),
        "spend_today": float(spend_today),
        "pending_approvals": int(pending_approvals),
        "open_tickets": int(open_tickets),
        # Guarded: a fresh install has no runs, and a ZeroDivisionError on
        # the landing screen would be an unusually poor first impression.
        "error_rate": (errors_today / runs_today) if runs_today else 0.0,
    }


def costs(db: Session) -> dict[str, Any]:
    """Spec 15's Costs screen. Grouped four ways because each answers a
    different question: by day (is spend growing), by model (what is
    expensive), by user (who is driving it), by trigger (which feature)."""
    by_day = [
        {"day": str(day), "cost_usd": float(total or 0)}
        for day, total in db.query(
            func.date(Run.started_at), func.coalesce(func.sum(Run.cost_usd), 0),
        ).group_by(func.date(Run.started_at)).order_by(func.date(Run.started_at)).all()
    ]
    by_trigger = [
        {"trigger": trigger.value, "cost_usd": float(total or 0), "runs": int(count)}
        for trigger, total, count in db.query(
            Run.trigger, func.coalesce(func.sum(Run.cost_usd), 0), func.count(Run.id),
        ).group_by(Run.trigger).all()
    ]
    by_model = [
        {"model": model or "unpriced", "cost_usd": float(total or 0), "calls": int(count)}
        for model, total, count in db.query(
            Span.model, func.coalesce(func.sum(Span.cost_usd), 0), func.count(Span.id),
        ).filter(Span.model.isnot(None)).group_by(Span.model).all()
    ]
    by_user = [
        {"username": username or "(guest)", "cost_usd": float(total or 0)}
        for username, total in db.query(
            User.username, func.coalesce(func.sum(Run.cost_usd), 0),
        ).select_from(Run).outerjoin(User, Run.user_id == User.id).group_by(User.username).all()
    ]

    totals_row = db.query(
        func.coalesce(func.sum(Run.input_tokens), 0),
        func.coalesce(func.sum(Run.output_tokens), 0),
        func.coalesce(func.sum(Run.cache_read_tokens), 0),
        func.coalesce(func.sum(Run.cache_write_tokens), 0),
        func.coalesce(func.sum(Run.cost_usd), 0),
    ).one()
    input_tokens, output_tokens, cache_read, cache_write, total_cost = totals_row

    # Cache hit rate is cache reads over everything that COULD have been a
    # cache read -- reads plus fresh input. Guarded for a fresh install.
    denominator = int(input_tokens) + int(cache_read)
    return {
        "by_day": by_day,
        "by_model": by_model,
        "by_user": by_user,
        "by_trigger": by_trigger,
        "totals": {
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "cache_read_tokens": int(cache_read),
            "cache_write_tokens": int(cache_write),
            "cost_usd": float(total_cost),
            "cache_hit_rate": (int(cache_read) / denominator) if denominator else 0.0,
        },
    }
