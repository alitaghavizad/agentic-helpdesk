"""Admin endpoints. Phase 6 adds only the two approvals routes from spec 14;
Phase 8 fills in the rest of the panel.

Both routes are `def`, not `async def`, on purpose. Approving a send_email
action performs a blocking SMTP call with a 10-second timeout; Starlette
runs a sync endpoint in a threadpool, so that block cannot stall the event
loop and therefore cannot stall every open notification SSE stream. Making
these async would silently reintroduce that.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.admin import queries
from app.admin.dossier import IncidentDossier
from app.admin.schemas import (
    AuditEntry, ConversationDetail, ConversationSummary, Costs, LessonDeleteResult,
    LessonSummary, Overview, PageResponse, RunSummary, RunTrace, SpanNode, TraceRun,
    UserPatchResult, UserSummary,
)
from app.approvals import service as approvals
from app.audit.service import record_audit
from app.chat.schemas import transcript_of
from app.db.models import (
    ActorType, ApprovalStatus, Clearance, Lesson, LessonStatus, Role, User,
)
from app.deps import DbSession, require_role
from app.learning import writer
from app.notifications import broker
from app.rbac.policy import Principal
from app.tracing.store import trace_tree

router = APIRouter(prefix="/api/admin", tags=["admin"])

AdminPrincipal = Annotated[Principal, Depends(require_role("admin"))]

_KEEPALIVE_SECONDS = 15


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



@router.get("/overview", response_model=Overview)
def admin_overview(principal: AdminPrincipal, db: DbSession) -> dict:
    return queries.overview(db)


@router.get("/costs", response_model=Costs)
def admin_costs(principal: AdminPrincipal, db: DbSession) -> dict:
    return queries.costs(db)


def _run_list_row(run) -> dict:
    """The `RunSummary` shape, shared by GET /runs and the runs list nested
    inside GET /admin/conversations/{id} -- extracted so the two lists
    cannot disagree about what a RunSummary is."""
    return {
        "id": str(run.id), "trigger": run.trigger.value, "status": run.status.value,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "duration_ms": run.duration_ms,
        "cost_usd": float(run.cost_usd) if run.cost_usd is not None else None,
        "llm_calls": run.llm_calls, "tool_calls": run.tool_calls, "error": run.error,
    }


@router.get("/runs", response_model=PageResponse[RunSummary])
def admin_runs(
    principal: AdminPrincipal, db: DbSession, limit: int | None = None, offset: int | None = None,
) -> PageResponse:
    """`limit`/`offset` are typed as plain optional ints rather than
    constrained ones on purpose: the clamp lives in queries.clamp_limit so
    that an over-large limit is answered with the maximum page instead of a
    422. See that docstring for why."""
    page = queries.list_runs(db, limit=limit, offset=offset)
    return PageResponse(
        items=[_run_list_row(r) for r in page.items],
        total=page.total, limit=page.limit, offset=page.offset,
    )


@router.get("/runs/stream")
async def admin_runs_stream(principal: AdminPrincipal) -> StreamingResponse:
    """Live run activity for the Traces screen.

    Takes NO `db: DbSession`, deliberately. FastAPI exits the dependency
    stack only after the response completes, which for an SSE stream means
    when the client disconnects; Phase 6 measured that a stream holding its
    request session pins a pooled Postgres connection `idle in transaction`
    for the life of that stream, and about fifteen such streams exhaust the
    pool (pool_size=5 + max_overflow=10), after which login, chat and
    approvals all block. There is no backlog to replay here -- run history
    is available from GET /api/admin/runs -- so this endpoint needs no
    session at all, and an idle stream therefore holds nothing.

    That also means there is no subscribe-before-read ordering hazard to
    manage, unlike the notification stream: with no snapshot read there is
    no window for an event to fall between the two sources.

    `async def`, unlike every other route in this module: the whole body is
    awaiting a queue, so there is nothing to push into a threadpool, and a
    sync generator could not await the broker at all.
    """
    async def event_stream():
        with broker.subscribe(broker.ADMIN_RUNS_CHANNEL) as subscription:
            while True:
                try:
                    event = await asyncio.wait_for(
                        subscription.get(), timeout=_KEEPALIVE_SECONDS,
                    )
                except asyncio.TimeoutError:
                    # A comment frame: proxies close a stream that has said
                    # nothing for long enough, and run activity is bursty.
                    yield ": keepalive\n\n"
                    continue
                except broker.SubscriberDropped:
                    # Fell too far behind and the broker stopped queueing for
                    # it. Close rather than pretend the feed is still live:
                    # the client reconnects and re-reads history from
                    # GET /api/admin/runs, so nothing durable is lost.
                    return
                yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
    """ONE span, with `children` left empty for the caller to fill.

    Deliberately NOT recursive, which is a reversal worth explaining: it
    used to recurse, and _span_forest then overwrote the `children` key it
    had just built. The output was right and the cap was useless -- the
    whole subtree was serialised and thrown away, so a trace large enough
    to need capping still paid the full cost of building it. The recursion
    lives in _span_forest now, where the cap can actually stop it.

    `input`/`output` are already redacted at persistence time by
    tracing/redaction.py -- this does not re-redact and must not be relied
    on to."""
    s = node.span
    return {
        "id": str(s.id), "kind": s.kind.value, "name": s.name, "status": s.status.value,
        "duration_ms": s.duration_ms, "model": s.model,
        "input_tokens": s.input_tokens, "output_tokens": s.output_tokens,
        "cache_read_tokens": s.cache_read_tokens, "cache_write_tokens": s.cache_write_tokens,
        "cost_usd": float(s.cost_usd) if s.cost_usd is not None else None,
        "input": s.input, "output": s.output, "error": s.error,
        "children": [],
    }


# A trace is unbounded in the data. One run's tree measured 167,617 bytes
# against this development database, and an agentic run with a long tool
# loop has no ceiling at all -- the panel would be asked to parse and lay
# out the whole thing in one response. The cap is generous enough that no
# ordinary run reaches it (the largest here has well under a hundred
# spans), so in practice it bounds only the pathological case.
_MAX_TRACE_SPANS = 500

# Depth needs its own bound, and not for display reasons. The response is
# validated against schemas.SpanNode, whose `children` is self-referential,
# and pydantic-core refuses to validate a structure nested past 99 --
# `recursion_loop`, surfacing as a ResponseValidationError and an HTTP 500
# from the very endpoint the span cap exists to keep answering. The span
# cap alone does not help: 150 spans in one chain is 150 deep and well
# under 500. Set below the limit with room to spare, since nothing in this
# database is deeper than 2 and anything approaching it is pathological.
_MAX_TRACE_DEPTH = 50


def _span_forest(roots, cap: int, max_depth: int = _MAX_TRACE_DEPTH) -> tuple[list[dict], int, bool]:
    """Serialises the waterfall depth-first, stopping after `cap` spans.

    Depth-first rather than breadth-first because the result is read as a
    waterfall: keeping each parent adjacent to the children it spawned
    means a truncated trace is a correct prefix of the real one, whereas a
    breadth-first cut would return every root with none of their bodies.

    Returns the nodes actually emitted, the number emitted, and whether
    anything was dropped -- the flag matters because a silently shortened
    waterfall reads as a run that simply stopped.
    """
    emitted = 0
    truncated = False

    def _walk(node, depth: int) -> dict | None:
        nonlocal emitted, truncated
        if emitted >= cap:
            truncated = True
            return None
        emitted += 1
        payload = _span_node(node)
        children = []
        if depth + 1 < max_depth:
            for child in node.children:
                serialised = _walk(child, depth + 1)
                if serialised is None:
                    break
                children.append(serialised)
        elif node.children:
            # Deeper than the response model can carry. The node itself is
            # kept and its subtree dropped, which is why the flag matters:
            # without it this reads as a span that called nothing.
            truncated = True
        payload["children"] = children
        return payload

    out = []
    for root in roots:
        serialised = _walk(root, 0)
        if serialised is None:
            break
        out.append(serialised)
    return out, emitted, truncated


@router.get("/runs/{run_id}/trace", response_model=RunTrace)
def admin_run_trace(run_id: uuid.UUID, principal: AdminPrincipal) -> dict:
    """Takes NO `db: DbSession`: trace_tree opens its own session by design
    (app/tracing/store.py), because a trace must be readable independently
    of any business transaction, so the dependency here was declared and
    never used. Measured, so as not to overstate it: an unused Session
    checks nothing out of the pool -- SQLAlchemy acquires a connection
    lazily, on the first statement -- so this cost nothing today. It is
    removed because it was one `db.query(...)` away from costing a pooled
    connection per request for a read that already has its own.

    trace_tree raises ValueError for an unknown run id; that is the only
    ValueError it can raise, so translating it to 404 cannot mask an
    unrelated failure."""
    try:
        tree = trace_tree(run_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such run")
    roots, span_count, truncated = _span_forest(tree.roots, _MAX_TRACE_SPANS)
    return {
        "run": _run_summary(tree.run),
        "roots": roots,
        "span_count": span_count,
        "truncated": truncated,
    }


def _conversation_summary_row(conv, username: str | None, full_name: str | None) -> dict:
    """The `ConversationSummary` shape, shared by the list and the detail
    endpoint below -- both read through `queries._conversations_with_participant`'s
    identical join, and this is what turns that join's tuple into the
    published schema, so the two endpoints cannot disagree about what a
    conversation's participant looks like."""
    return {
        "id": str(conv.id), "title": conv.title, "status": conv.status.value,
        "user_id": str(conv.user_id) if conv.user_id else None,
        "guest_name": conv.guest_name, "guest_email": conv.guest_email,
        "username": username, "full_name": full_name,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
    }


@router.get("/conversations", response_model=PageResponse[ConversationSummary])
def admin_conversations(
    principal: AdminPrincipal, db: DbSession, q: str | None = None,
    limit: int | None = None, offset: int | None = None,
) -> PageResponse:
    page = queries.list_conversations(db, q=q, limit=limit, offset=offset)
    return PageResponse(
        items=[_conversation_summary_row(conv, username, full_name) for conv, username, full_name in page.items],
        total=page.total, limit=page.limit, offset=page.offset,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def admin_conversation_detail(
    conversation_id: uuid.UUID, principal: AdminPrincipal, db: DbSession,
) -> ConversationDetail:
    """Not audited, like every other admin read. The audit log records
    mutating calls (spec 14); a row per detail view would bury real events
    under navigation noise."""
    row = queries.get_conversation_with_participant(db, conversation_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such conversation")
    conv, username, full_name = row

    return ConversationDetail(
        conversation=ConversationSummary(**_conversation_summary_row(conv, username, full_name)),
        messages=transcript_of(db, conversation_id),
        runs=[RunSummary(**_run_list_row(run)) for run in queries.conversation_runs(db, conversation_id)],
    )


@router.get("/audit", response_model=PageResponse[AuditEntry])
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


@router.get("/users", response_model=PageResponse[UserSummary])
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


@router.patch("/users/{user_id}", response_model=UserPatchResult)
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


@router.post("/tickets/{ticket_id}/dossier", response_model=IncidentDossier)
def admin_ticket_dossier(
    ticket_id: uuid.UUID, principal: AdminPrincipal, db: DbSession,
) -> dict:
    """Sync `def`, like the approvals routes and for the same reason: this
    performs a multi-second blocking model call, and Starlette runs a sync
    endpoint in a threadpool so that block cannot stall the event loop and
    therefore cannot stall every open SSE stream.

    The client is fetched only AFTER the ticket is found, so a 404 neither
    requires an API key to be configured nor risks paying for a call with
    nothing to summarise.
    """
    from app.admin import dossier as dossier_module
    from app.admin.dossier import DossierFailed, build_dossier, gather_material
    from app.db.models import Ticket

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).one_or_none()
    if ticket is None:
        # Not audited: nothing was disclosed and nothing was spent, and a
        # row here would let any admin pad the log with entries for tickets
        # that never existed.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such ticket")

    material = gather_material(db, ticket)

    # RELEASE THE CONNECTION BEFORE THE MODEL CALL. Everything above read
    # through `db`, which holds a pooled connection and an open transaction
    # until this commit. The call below takes tens of seconds (36.5s on
    # this project's own live run) and itself needs a second connection for
    # the run it traces, so holding this one across it means concurrent
    # dossier builds deadlock against their own pool -- measured on a real
    # server: 20 concurrent builds, 7 of them 500 after the 30s pool
    # timeout, 14 backends sitting `idle in transaction`. There is nothing
    # to lose by committing: this handler only read.
    db.commit()

    try:
        result = build_dossier(dossier_module._get_sync_client(), material)
    except Exception as exc:  # noqa: BLE001 -- re-raised below, never swallowed
        # Catches Exception, not just DossierFailed. The audit row records a
        # disclosure and a spend that have already happened by the time most
        # failures surface, so a narrower catch would skip it on exactly the
        # paths that most need it.
        #
        # Rollback FIRST: a database error would leave this session poisoned
        # and turn the audit write into a 500 that buries the real failure --
        # the shape of the phase 6 defect where a handler's DB error stopped
        # an approval ever being marked failed.
        db.rollback()
        _audit_dossier(db, principal, ticket_id, outcome="failed", detail=str(exc))
        if isinstance(exc, DossierFailed):
            # 502, not 500: the failure is upstream, and the distinction
            # matters to whoever is reading the logs at 3am.
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        # Anything else is our bug, and a 500 is the honest answer.
        raise

    _audit_dossier(db, principal, ticket_id, outcome="ok")
    return result.model_dump()


def _audit_dossier(
    db, principal, ticket_id: uuid.UUID, *, outcome: str, detail: str | None = None,
) -> None:
    """Audited even though building a dossier mutates nothing.

    It is a read, so the gate's "every mutation is audited" clause does not
    reach it -- but it is the one read on this surface that discloses a
    whole conversation transcript, including whatever a user typed and
    whatever an attachment said, and it spends real money doing so. Those
    are exactly the questions an audit log exists to answer afterwards:
    who pulled this transcript, and when.

    Failures are recorded too, not just successes. The transcript reached
    the model and the call was billed whether or not a valid dossier came
    back, so an audit trail that only showed successes would understate
    both the disclosure and the spend.
    """
    payload = {"outcome": outcome}
    if detail is not None:
        # Bounded: DossierFailed can carry a whole pydantic ValidationError,
        # and the audit log is not the place to store one in full.
        payload["detail"] = detail[:500]
    record_audit(
        db, actor_type=ActorType.USER, actor_id=principal.user_id,
        action="dossier.built", target_type="ticket", target_id=str(ticket_id),
        payload=payload,
    )
    db.commit()


def _lesson_row(lesson) -> dict:
    """The `LessonSummary` shape, shared by the list endpoint and the PATCH
    response -- extracted so the two cannot disagree about what a
    LessonSummary is."""
    return {
        "id": str(lesson.id), "title": lesson.title, "category": lesson.category,
        "content_md": lesson.content_md, "status": lesson.status.value,
        "confidence": lesson.confidence.value,
        "ticket_id": str(lesson.ticket_id) if lesson.ticket_id else None,
        "created_at": lesson.created_at.isoformat() if lesson.created_at else None,
    }


@router.get("/lessons", response_model=PageResponse[LessonSummary])
def admin_lessons(
    principal: AdminPrincipal, db: DbSession, limit: int | None = None, offset: int | None = None,
) -> PageResponse:
    page = queries.list_lessons(db, limit=limit, offset=offset)
    return PageResponse(
        items=[_lesson_row(lesson) for lesson in page.items],
        total=page.total, limit=page.limit, offset=page.offset,
    )


@router.patch("/lessons/{lesson_id}", response_model=LessonSummary)
async def admin_patch_lesson(
    lesson_id: uuid.UUID, payload: LessonPatch, principal: AdminPrincipal, db: DbSession,
) -> dict:
    """Same single-transaction contract as admin_patch_user: flush the change,
    stage the audit row, one commit.

    Returns the full LessonSummary, not just {id, status}, so the panel can
    re-render the edited row from the response instead of re-fetching the
    list."""
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

    try:
        await writer.upsert_embedding(lesson)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "could not update the lesson's embedding; try again") from exc

    db.commit()
    return _lesson_row(lesson)


@router.delete("/lessons/{lesson_id}", response_model=LessonDeleteResult)
async def admin_archive_lesson(
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

    try:
        await writer.upsert_embedding(lesson)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "could not update the lesson's embedding; try again") from exc

    db.commit()
    return {"id": str(lesson.id), "status": lesson.status.value, "archived": True}
