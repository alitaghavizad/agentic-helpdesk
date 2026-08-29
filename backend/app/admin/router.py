"""Admin endpoints. Phase 6 adds only the two approvals routes from spec 14;
Phase 8 fills in the rest of the panel.

Both routes are `def`, not `async def`, on purpose. Approving a send_email
action performs a blocking SMTP call with a 10-second timeout; Starlette
runs a sync endpoint in a threadpool, so that block cannot stall the event
loop and therefore cannot stall every open notification SSE stream. Making
these async would silently reintroduce that.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.admin import queries
from app.approvals import service as approvals
from app.audit.service import record_audit
from app.db.models import (
    ActorType, ApprovalStatus, Clearance, Lesson, LessonStatus, Role, User,
)
from app.deps import DbSession, require_role
from app.rbac.policy import Principal
from app.tracing.store import trace_tree

router = APIRouter(prefix="/api/admin", tags=["admin"])

AdminPrincipal = Annotated[Principal, Depends(require_role("admin"))]


class DecideRequest(BaseModel):
    approve: bool
    note: str = ""


class ApprovalResponse(BaseModel):
    id: str
    request_number: str
    conversation_id: str
    action_type: str
    action_payload: dict
    justification: str
    risk_level: str
    agent_summary: str
    status: str
    decision_note: str | None
    execution_result: dict | None


def _serialize(request) -> ApprovalResponse:
    return ApprovalResponse(
        id=str(request.id),
        request_number=f"REQ-{request.request_number:06d}",
        conversation_id=str(request.conversation_id),
        action_type=request.action_type.value,
        action_payload=request.action_payload,
        justification=request.justification,
        risk_level=request.risk_level.value,
        agent_summary=request.agent_summary,
        status=request.status.value,
        decision_note=request.decision_note,
        execution_result=request.execution_result,
    )


@router.get("/approvals", response_model=list[ApprovalResponse])
def list_approvals(principal: AdminPrincipal, db: DbSession, status: str | None = None) -> list[ApprovalResponse]:
    parsed = None
    if status is not None:
        try:
            parsed = ApprovalStatus(status)
        except ValueError:
            raise HTTPException(422, f"unknown approval status {status!r}")
    return [_serialize(r) for r in approvals.list_for_admin(db, status=parsed)]


@router.post("/approvals/{request_id}/decide", response_model=ApprovalResponse)
def decide_approval(
    request_id: uuid.UUID, payload: DecideRequest, principal: AdminPrincipal, db: DbSession,
) -> ApprovalResponse:
    # Check existence explicitly instead of catching LookupError around
    # decide(): KeyError and IndexError are both LookupError subclasses, so
    # `except LookupError` here would also swallow an unrelated bug inside
    # decide() -- e.g. a bad dict/list lookup somewhere in the executor --
    # and misreport it as 404 "no such approval request" instead of letting
    # it surface as a 500 that gets noticed and fixed.
    if approvals.get(db, request_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such approval request")
    try:
        decided = approvals.decide(db, principal, request_id, approve=payload.approve, note=payload.note)
    except approvals.NotPending as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return _serialize(decided)


# ---- Read endpoints (spec 15) ------------------------------------------------------

class PageResponse(BaseModel):
    """One envelope for every list endpoint. `total` is the count BEFORE
    limit/offset, so a client can render a pager without walking the whole
    result set."""
    items: list[dict]
    total: int
    limit: int
    offset: int


@router.get("/overview")
def admin_overview(principal: AdminPrincipal, db: DbSession) -> dict:
    return queries.overview(db)


@router.get("/costs")
def admin_costs(principal: AdminPrincipal, db: DbSession) -> dict:
    return queries.costs(db)


@router.get("/runs", response_model=PageResponse)
def admin_runs(
    principal: AdminPrincipal, db: DbSession, limit: int | None = None, offset: int | None = None,
) -> PageResponse:
    """`limit`/`offset` are typed as plain optional ints rather than
    constrained ones on purpose: the clamp lives in queries.clamp_limit so
    that an over-large limit is answered with the maximum page instead of a
    422. See that docstring for why."""
    page = queries.list_runs(db, limit=limit, offset=offset)
    return PageResponse(
        items=[{
            "id": str(r.id), "trigger": r.trigger.value, "status": r.status.value,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "duration_ms": r.duration_ms,
            "cost_usd": float(r.cost_usd) if r.cost_usd is not None else None,
            "llm_calls": r.llm_calls, "tool_calls": r.tool_calls, "error": r.error,
        } for r in page.items],
        total=page.total, limit=page.limit, offset=page.offset,
    )


def _run_summary(run) -> dict:
    return {
        "id": str(run.id), "trigger": run.trigger.value, "status": run.status.value,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "duration_ms": run.duration_ms,
        "cost_usd": float(run.cost_usd) if run.cost_usd is not None else None,
        "input_tokens": run.input_tokens, "output_tokens": run.output_tokens,
        "cache_read_tokens": run.cache_read_tokens, "cache_write_tokens": run.cache_write_tokens,
        "error": run.error,
    }


def _span_node(node) -> dict:
    """Recursive, matching the waterfall spec 15 describes. `input`/`output`
    are already redacted at persistence time by tracing/redaction.py -- this
    does not re-redact and must not be relied on to."""
    s = node.span
    return {
        "id": str(s.id), "kind": s.kind.value, "name": s.name, "status": s.status.value,
        "duration_ms": s.duration_ms, "model": s.model,
        "input_tokens": s.input_tokens, "output_tokens": s.output_tokens,
        "cache_read_tokens": s.cache_read_tokens, "cache_write_tokens": s.cache_write_tokens,
        "cost_usd": float(s.cost_usd) if s.cost_usd is not None else None,
        "input": s.input, "output": s.output, "error": s.error,
        "children": [_span_node(c) for c in node.children],
    }


@router.get("/runs/{run_id}/trace")
def admin_run_trace(run_id: uuid.UUID, principal: AdminPrincipal, db: DbSession) -> dict:
    """Takes `db` only to keep the signature uniform with its neighbours --
    trace_tree opens its own session by design (app/tracing/store.py), because
    a trace must be readable independently of any business transaction.

    trace_tree raises ValueError for an unknown run id; that is the only
    ValueError it can raise, so translating it to 404 cannot mask an
    unrelated failure."""
    try:
        tree = trace_tree(run_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such run")
    return {"run": _run_summary(tree.run), "roots": [_span_node(n) for n in tree.roots]}


@router.get("/conversations", response_model=PageResponse)
def admin_conversations(
    principal: AdminPrincipal, db: DbSession, q: str | None = None,
    limit: int | None = None, offset: int | None = None,
) -> PageResponse:
    page = queries.list_conversations(db, q=q, limit=limit, offset=offset)
    return PageResponse(
        items=[{
            "id": str(c.id), "title": c.title, "status": c.status.value,
            "user_id": str(c.user_id) if c.user_id else None,
            "guest_name": c.guest_name, "guest_email": c.guest_email,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        } for c in page.items],
        total=page.total, limit=page.limit, offset=page.offset,
    )


@router.get("/audit", response_model=PageResponse)
def admin_audit(
    principal: AdminPrincipal, db: DbSession, actor_id: str | None = None,
    action: str | None = None, target_type: str | None = None,
    since: datetime | None = None, until: datetime | None = None,
    limit: int | None = None, offset: int | None = None,
) -> PageResponse:
    """`since`/`until` are ISO-8601 query params, which FastAPI parses into
    datetimes natively (and rejects with a 422 when unparseable, which is the
    right answer for a malformed bound). They bound `created_at` half-open,
    [since, until); a bound with no offset is read as UTC. See
    queries.list_audit and queries._as_utc for why on both counts."""
    page = queries.list_audit(
        db, actor_id=actor_id, action=action, target_type=target_type,
        since=since, until=until, limit=limit, offset=offset,
    )
    return PageResponse(
        items=[{
            "id": str(a.id), "actor_type": a.actor_type.value, "actor_id": a.actor_id,
            "action": a.action, "target_type": a.target_type, "target_id": a.target_id,
            "payload": a.payload, "ip_address": a.ip_address,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        } for a in page.items],
        total=page.total, limit=page.limit, offset=page.offset,
    )


# ---- Mutating endpoints: users and lessons (spec 14, 4.2) --------------------------

class UserPatch(BaseModel):
    """Role and clearance, and nothing else.

    `extra: "ignore"` is Pydantic's default, but it is stated explicitly
    because it is load-bearing here rather than incidental: it is what turns
    an attempt to smuggle `password_hash`, `username` or `id` through this
    route into a no-op instead of a privilege escalation that no audit row
    would explain. tests/test_admin_mutations.py exercises it directly, so
    the behaviour cannot be changed silently.

    Both fields are optional and an omitted one means "leave it alone", not
    "set it to null" -- a PATCH that cleared clearance on every role change
    would quietly demote privileged accounts. The enum types do the
    validation: an unknown role is a 422 before the handler runs, so the row
    is never touched.
    """
    model_config = {"extra": "ignore"}
    role: Role | None = None
    clearance: Clearance | None = None


class LessonPatch(BaseModel):
    """Content, title and status. Not `category`, `file_path`, `ticket_id` or
    `created_by_run_id`: those are the lesson's provenance, the chain that
    makes it auditable at all, and an admin correcting the text of a lesson
    must not be able to re-attribute it to a different ticket or run."""
    model_config = {"extra": "ignore"}
    content_md: str | None = None
    title: str | None = None
    status: LessonStatus | None = None


@router.get("/users", response_model=PageResponse)
def admin_users(
    principal: AdminPrincipal, db: DbSession, limit: int | None = None, offset: int | None = None,
) -> PageResponse:
    """The payload is an explicit dict rather than a dump of the row, so a
    column cannot join the API by being added to the table -- `password_hash`
    sits on the same object."""
    page = queries.list_users(db, limit=limit, offset=offset)
    return PageResponse(
        items=[{
            "id": str(u.id), "username": u.username, "email": u.email,
            "full_name": u.full_name, "role": u.role.value,
            "clearance": u.clearance.value if u.clearance else None,
            "department": u.department, "employee_ref": u.employee_ref,
            "helpdesk_ref": u.helpdesk_ref, "is_active": u.is_active,
            # The 125 accounts seeded from the EMP-xxx and HD-xxx profiles all
            # share SEED_USER_PASSWORD (spec 5.6 item 4), so the panel marks
            # them rather than implying they are real accounts with real
            # credentials. `employee_ref`/`helpdesk_ref` is the derivation
            # because app/db/seed.py sets those columns on exactly those two
            # populations and on nothing else.
            #
            # The `admin` account is NOT flagged, deliberately: seed.py builds
            # it from ADMIN_PASSWORD (item 1), not the shared password, so a
            # badge saying otherwise would be false about the one account
            # whose credentials matter most. "Seeded" and "shares the dev
            # password" differ by exactly that row, and this flag means the
            # second. (Measured: 125 of the 126 seeded users.)
            "dev_seed": u.employee_ref is not None or u.helpdesk_ref is not None,
        } for u in page.items],
        total=page.total, limit=page.limit, offset=page.offset,
    )


@router.patch("/users/{user_id}")
def admin_patch_user(
    user_id: uuid.UUID, payload: UserPatch, principal: AdminPrincipal, db: DbSession,
) -> dict:
    """Changes role and/or clearance and audits the change.

    The mutation and its `audit_log` row share ONE transaction: the change is
    flushed, `record_audit` stages and flushes its row (it never commits --
    see app/audit/service.py), and a single `db.commit()` at the end makes
    both durable together. An audit entry that survived a rolled-back
    mutation would be a forensic lie, and one lost while the mutation stuck
    would be a silent gap; neither is possible while there is exactly one
    commit. tests/test_admin_mutations.py injects a failure after the audit
    row is staged and proves nothing survives.

    The previous values go into the payload as well as the new ones: an audit
    row saying only what a field became cannot answer "what changed", which
    is the question the table exists for.
    """
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such user")

    previous = {
        "previous_role": user.role.value,
        "previous_clearance": user.clearance.value if user.clearance else None,
    }
    if payload.role is not None:
        user.role = payload.role
    if payload.clearance is not None:
        user.clearance = payload.clearance
    db.flush()

    record_audit(
        db, actor_type=ActorType.USER, actor_id=principal.user_id,
        action="user.updated", target_type="user", target_id=str(user.id),
        payload={
            **previous,
            "new_role": user.role.value,
            "new_clearance": user.clearance.value if user.clearance else None,
        },
    )
    db.commit()
    return {
        "id": str(user.id), "role": user.role.value,
        "clearance": user.clearance.value if user.clearance else None,
    }


@router.get("/lessons", response_model=PageResponse)
def admin_lessons(
    principal: AdminPrincipal, db: DbSession, limit: int | None = None, offset: int | None = None,
) -> PageResponse:
    page = queries.list_lessons(db, limit=limit, offset=offset)
    return PageResponse(
        items=[{
            "id": str(lesson.id), "title": lesson.title, "category": lesson.category,
            "content_md": lesson.content_md, "status": lesson.status.value,
            "confidence": lesson.confidence.value,
            "ticket_id": str(lesson.ticket_id) if lesson.ticket_id else None,
            "created_at": lesson.created_at.isoformat() if lesson.created_at else None,
        } for lesson in page.items],
        total=page.total, limit=page.limit, offset=page.offset,
    )


@router.patch("/lessons/{lesson_id}")
def admin_patch_lesson(
    lesson_id: uuid.UUID, payload: LessonPatch, principal: AdminPrincipal, db: DbSession,
) -> dict:
    """Same single-transaction contract as admin_patch_user: flush the change,
    stage the audit row, one commit."""
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id).one_or_none()
    if lesson is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such lesson")

    for field in ("content_md", "title", "status"):
        value = getattr(payload, field)
        if value is not None:
            setattr(lesson, field, value)
    db.flush()

    record_audit(
        db, actor_type=ActorType.USER, actor_id=principal.user_id,
        action="lesson.updated", target_type="lesson", target_id=str(lesson.id),
        payload={"status": lesson.status.value},
    )
    db.commit()
    return {"id": str(lesson.id), "status": lesson.status.value}


@router.delete("/lessons/{lesson_id}")
def admin_archive_lesson(
    lesson_id: uuid.UUID, principal: AdminPrincipal, db: DbSession,
) -> dict:
    """DELETE archives; it does not remove the row.

    Spec 4.2 and parent spec 20 make lessons archivable so a bad lesson can be
    withdrawn from retrieval without destroying the record that it existed and
    was acted on. The row survives, the list keeps returning it, and the audit
    action says `lesson.archived` rather than `lesson.deleted` for the same
    reason.

    Repeating the call on an already-archived lesson is idempotent -- 200 and
    the same body -- rather than a 409. The verb states a desired end state,
    that end state already holds, and a panel whose delete button errors on a
    double-click is worse than one that does nothing. The second audit row is
    NOT suppressed: it records that an admin issued the request, which is true
    whether or not the row changed, and dropping it because the write was a
    no-op is how an audit trail starts lying by omission.
    """
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id).one_or_none()
    if lesson is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such lesson")

    lesson.status = LessonStatus.ARCHIVED
    db.flush()
    record_audit(
        db, actor_type=ActorType.USER, actor_id=principal.user_id,
        action="lesson.archived", target_type="lesson", target_id=str(lesson.id),
        payload={},
    )
    db.commit()
    return {"id": str(lesson.id), "status": lesson.status.value, "archived": True}
