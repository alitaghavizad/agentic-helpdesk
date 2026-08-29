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
    ApprovalRequest, ApprovalStatus, AuditLog, Conversation, Run, RunStatus, Span, Ticket,
    TicketStatus, User,
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
    # The error-rate denominator, unlike runs_today. A run still RUNNING has
    # not had the chance to fail yet, so counting it dilutes the rate exactly
    # when a burst of traffic is in flight -- the moment the number matters
    # most. ABORTED is deliberately NOT excluded: per app/agent/budget.py it
    # is a budget cutoff, which is a completed outcome and a real non-error,
    # so it belongs in the denominator and out of the numerator.
    completed_today = db.query(func.count(Run.id)).filter(
        Run.started_at >= since, Run.status != RunStatus.RUNNING,
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
        # Counts every run including the in-flight ones: this counter
        # answers "how much happened today", and work in progress happened.
        "runs_today": int(runs_today),
        "spend_today": float(spend_today),
        "pending_approvals": int(pending_approvals),
        "open_tickets": int(open_tickets),
        # Guarded: a fresh install has no runs, and a ZeroDivisionError on
        # the landing screen would be an unusually poor first impression.
        "error_rate": (errors_today / completed_today) if completed_today else 0.0,
    }


def costs(db: Session) -> dict[str, Any]:
    """Spec 15's Costs screen. Grouped four ways because each answers a
    different question: by day (is spend growing), by model (what is
    expensive), by user (who is driving it), by trigger (which feature)."""
    # Group by the UTC calendar day, explicitly. Run.started_at is a
    # timestamptz, so a bare func.date() would render it in whatever the
    # Postgres session's TimeZone happens to be, while _start_of_today() is
    # unconditionally UTC -- two panels of the same screen could then
    # disagree about which day a run belongs to. Pinning both to UTC is what
    # makes "today" mean one thing.
    _utc_day = func.date(func.timezone("UTC", Run.started_at))
    by_day = [
        {"day": str(day), "cost_usd": float(total or 0)}
        for day, total in db.query(
            _utc_day, func.coalesce(func.sum(Run.cost_usd), 0),
        ).group_by(_utc_day).order_by(_utc_day).all()
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

    # Cache hit rate is cache reads over every prompt-side token processed,
    # not just reads plus fresh input. Omitting cache WRITES makes a workload
    # that is establishing a cache -- the first turn of every new
    # conversation -- report a near-perfect hit rate while barely benefiting
    # from caching at all. Guarded for a fresh install.
    denominator = int(input_tokens) + int(cache_read) + int(cache_write)
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


def list_runs(db: Session, *, limit: int | None = None, offset: int | None = None) -> Page:
    """Newest first, which is the only ordering an operator ever wants from a
    run list. The `id` tiebreaker is not decoration: `started_at` defaults to
    `func.now()`, which in Postgres is TRANSACTION-start time, so every run
    opened inside one transaction shares a timestamp exactly. Without a
    stable second key the sort would fall through to physical row order and
    `offset` would silently return overlapping or skipped pages."""
    limit, offset = clamp_limit(limit), clamp_offset(offset)
    total = db.query(func.count(Run.id)).scalar() or 0
    items = (
        db.query(Run)
        .order_by(Run.started_at.desc(), Run.id.desc())
        .limit(limit).offset(offset).all()
    )
    return Page(items=items, total=int(total), limit=limit, offset=offset)


def list_conversations(
    db: Session, *, q: str | None = None, limit: int | None = None, offset: int | None = None,
) -> Page:
    """`q` matches the title OR the guest name, case-insensitively. An admin
    chasing a conversation remembers one of those; nobody remembers a uuid.

    `total` is the count of MATCHING rows, not of the table -- a search whose
    total ignores its own filter makes the pager lie about how many pages
    exist."""
    limit, offset = clamp_limit(limit), clamp_offset(offset)
    query = db.query(Conversation)
    if q:
        pattern = f"%{q}%"
        query = query.filter(
            Conversation.title.ilike(pattern) | Conversation.guest_name.ilike(pattern)
        )
    total = query.with_entities(func.count(Conversation.id)).scalar() or 0
    items = (
        query.order_by(Conversation.created_at.desc(), Conversation.id.desc())
        .limit(limit).offset(offset).all()
    )
    return Page(items=items, total=int(total), limit=limit, offset=offset)


def list_audit(
    db: Session, *, actor_id: str | None = None, action: str | None = None,
    target_type: str | None = None, limit: int | None = None, offset: int | None = None,
) -> Page:
    """The three filters are exact matches, deliberately. `audit_log` is the
    append-only record spec 5.4 defines, and an investigator asking "what did
    actor X do" wants precisely X's rows -- a substring match would silently
    fold in a different actor whose id happens to contain this one."""
    limit, offset = clamp_limit(limit), clamp_offset(offset)
    query = db.query(AuditLog)
    if actor_id:
        query = query.filter(AuditLog.actor_id == actor_id)
    if action:
        query = query.filter(AuditLog.action == action)
    if target_type:
        query = query.filter(AuditLog.target_type == target_type)
    total = query.with_entities(func.count(AuditLog.id)).scalar() or 0
    items = (
        query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit).offset(offset).all()
    )
    return Page(items=items, total=int(total), limit=limit, offset=offset)
