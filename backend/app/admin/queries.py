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


# The escape character handed to ILIKE. A single backslash in SQL; doubled
# here only because it is a Python string escape.
_LIKE_ESCAPE = "\\"


def _contains(term: str) -> str:
    """Builds a `%term%` ILIKE pattern with the metacharacters neutralised.

    `%`, `_` and `\\` are wildcards to ILIKE, so an unescaped search box does
    something other than substring search: `q=%` matches every row in the
    table (a full-table dump from a search field), and `q=P_inter` matches
    "Printer" because `_` is any-single-character. Both are silent -- the
    caller gets results, just not the ones they asked for.

    The backslash is escaped FIRST; doing it last would re-escape the
    backslashes introduced by the `%` and `_` replacements and turn the
    escapes back into literals."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalises a filter bound to an aware UTC datetime.

    Every `*_at` column in this schema is `timestamptz`, but FastAPI parses
    `?since=2026-08-29T00:00:00` (no offset) into a NAIVE datetime perfectly
    happily. A naive bind parameter is sent to Postgres without a zone and
    interpreted in the server session's TimeZone, so the identical request
    would select a different window depending on how the database happens to
    be configured. Naive input is therefore DEFINED to mean UTC -- the zone
    every timestamp this API emits is already in -- rather than being
    compared against whatever zone the server was started with."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


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
    run list. The `id` tiebreaker is not decoration: without a stable second
    key the sort would fall through to physical row order and `offset` would
    silently return overlapping or skipped pages. `started_at` is stamped
    per run in app/tracing/store.py::insert_run rather than left to the
    column's `func.now()` server default -- see the comment there -- so ties
    are now rare, but the pager must not depend on that."""
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
    """`q` matches the title OR the participant, case-insensitively (spec 4:
    "searchable by title and participant"). An admin chasing a conversation
    remembers one of those; nobody remembers a uuid.

    "Participant" needs the join. `conversations` carries a CHECK constraint
    (`(user_id IS NOT NULL) <> (guest_name IS NOT NULL AND guest_email IS NOT
    NULL)`) that makes guest_name GUARANTEED NULL whenever user_id is set, so
    searching guest_name alone can never match a single logged-in user's
    conversation -- participant search would be 0%-effective for exactly the
    population the admin panel exists to support. The user's username and
    full_name come from the joined `users` row; guest_email is searched too
    because it is the only identifier a guest conversation reliably has.

    The join is an OUTER join: a guest conversation has user_id NULL, and an
    inner join would silently drop every guest conversation from the
    unfiltered list as well as from search results.

    `total` is the count of MATCHING rows, not of the table -- a search whose
    total ignores its own filter makes the pager lie about how many pages
    exist. It is computed from the SAME query object, join included, so the
    two cannot drift apart."""
    limit, offset = clamp_limit(limit), clamp_offset(offset)
    query = db.query(Conversation).outerjoin(User, Conversation.user_id == User.id)
    # `q and q.strip()`, deliberately: `?q=` (an empty or whitespace-only
    # search box) lists the whole table rather than returning nothing. That is
    # what a search box should do when it is cleared, and spelling it out here
    # makes it a decision rather than the accidental falsiness of `if q:`.
    if q and q.strip():
        # The STRIPPED term, matching what was just tested. Searching the raw
        # value made `?q=%20printer%20` look for " printer " and find nothing,
        # so a search box that trailed a space silently returned an empty
        # table -- indistinguishable from "no such conversation".
        pattern = _contains(q.strip())
        query = query.filter(
            Conversation.title.ilike(pattern, escape=_LIKE_ESCAPE)
            | Conversation.guest_name.ilike(pattern, escape=_LIKE_ESCAPE)
            | Conversation.guest_email.ilike(pattern, escape=_LIKE_ESCAPE)
            | User.username.ilike(pattern, escape=_LIKE_ESCAPE)
            | User.full_name.ilike(pattern, escape=_LIKE_ESCAPE)
        )
    total = query.with_entities(func.count(Conversation.id)).scalar() or 0
    items = (
        query.order_by(Conversation.created_at.desc(), Conversation.id.desc())
        .limit(limit).offset(offset).all()
    )
    return Page(items=items, total=int(total), limit=limit, offset=offset)


def list_audit(
    db: Session, *, actor_id: str | None = None, action: str | None = None,
    target_type: str | None = None, since: datetime | None = None,
    until: datetime | None = None, limit: int | None = None, offset: int | None = None,
) -> Page:
    """Spec 4: "Filterable by actor, action, target type, date range".

    The three string filters are exact matches, deliberately. `audit_log` is
    the append-only record spec 5.4 defines, and an investigator asking "what
    did actor X do" wants precisely X's rows -- a substring match would
    silently fold in a different actor whose id happens to contain this one.

    `since`/`until` bound `created_at` half-open, [since, until): a row
    stamped exactly at `since` is INCLUDED and one stamped exactly at `until`
    is EXCLUDED. That is what makes two adjacent windows (…09:00, 09:00…)
    tile the timeline exactly -- a closed upper bound would report the row on
    the boundary in both windows, so paging through a day hour by hour would
    double-count it."""
    limit, offset = clamp_limit(limit), clamp_offset(offset)
    query = db.query(AuditLog)
    # `x and x.strip()`, deliberately: an empty or whitespace-only filter
    # value means "no filter", not "match the empty string". No audit row has
    # a blank action or target_type, so the alternative reading would return
    # nothing at all from a cleared filter box. Spelled out rather than left
    # to the falsiness of `if x:` so it reads as the decision it is.
    if actor_id and actor_id.strip():
        query = query.filter(AuditLog.actor_id == actor_id)
    if action and action.strip():
        query = query.filter(AuditLog.action == action)
    if target_type and target_type.strip():
        query = query.filter(AuditLog.target_type == target_type)
    if since is not None:
        query = query.filter(AuditLog.created_at >= _as_utc(since))
    if until is not None:
        query = query.filter(AuditLog.created_at < _as_utc(until))
    total = query.with_entities(func.count(AuditLog.id)).scalar() or 0
    items = (
        query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit).offset(offset).all()
    )
    return Page(items=items, total=int(total), limit=limit, offset=offset)


def list_users(db: Session, *, limit: int | None = None, offset: int | None = None) -> Page:
    """Ordered by username, which is both the only ordering an admin scanning
    a directory can navigate and -- because `users.username` carries a UNIQUE
    constraint -- a total order. That matters more than aesthetics: a sort key
    with ties falls through to physical row order, and `offset` then silently
    returns overlapping or skipped pages. No second tiebreaker is needed here
    for exactly that reason.

    Every user is listed, inactive ones included. `is_active` is in the
    payload so the panel can mark them; filtering them out would hide the
    accounts an admin most often needs to find."""
    limit, offset = clamp_limit(limit), clamp_offset(offset)
    total = db.query(func.count(User.id)).scalar() or 0
    items = db.query(User).order_by(User.username.asc()).limit(limit).offset(offset).all()
    return Page(items=items, total=int(total), limit=limit, offset=offset)


def conversation_runs(db: Session, conversation_id) -> list[Run]:
    """Every run belonging to one conversation, newest first, so the detail
    screen can link each into GET /api/admin/runs/{id}/trace.

    Unpaginated, unlike every other list in this module: runs are bounded by
    the turns in a single conversation, not by the size of the table.
    """
    return (
        db.query(Run)
        .filter(Run.conversation_id == conversation_id)
        .order_by(Run.started_at.desc(), Run.id.desc())
        .all()
    )


def list_lessons(db: Session, *, limit: int | None = None, offset: int | None = None) -> Page:
    """Newest first, with the `id` tiebreaker for the same reason as
    `list_runs`: `lessons.created_at` is the column's `func.now()` server
    default, which is TRANSACTION-start time, so a batch of lessons written by
    one run all share a byte-identical timestamp and the pager would otherwise
    have no stable second key.

    ARCHIVED lessons are deliberately NOT filtered out. Spec 4.2 makes DELETE
    an archive precisely so the record survives review; a list that hid
    archived rows would make the withdrawn lesson -- the one an admin is most
    likely to be looking for -- the only one they could not find."""
    from app.db.models import Lesson

    limit, offset = clamp_limit(limit), clamp_offset(offset)
    total = db.query(func.count(Lesson.id)).scalar() or 0
    items = (
        db.query(Lesson)
        .order_by(Lesson.created_at.desc(), Lesson.id.desc())
        .limit(limit).offset(offset).all()
    )
    return Page(items=items, total=int(total), limit=limit, offset=offset)
