# Tickets, Routing, Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give tickets a real life beyond creation — a validated status state machine, the four `/api/tickets` HTTP endpoints with spec §6.4 row scoping, specialist routing relocated to its spec-mandated home, and the first `audit_log` writes in the codebase — so that a conversation demonstrably yields a `tasks` row and a ticket assigned to a specialist whose specialization matches the category.

**Architecture:** `app/tickets/` becomes the complete ticket domain: `routing.py` (specialist ranking, extracted from `agent/tools/routing.py`), `service.py` (creation + lifecycle transitions + row scoping), and a new `router.py` (HTTP). A new `app/audit/service.py` provides the single append-only `audit_log` write path, used by the mutating ticket endpoints and by the RBAC denial path that spec §6.3 has required since Phase 4 but which was never implemented. Agent tool handlers in `agent/tools/` become thin wrappers over `app/tickets/`, matching the pattern Phase 4 already established for `tools/tickets.py` → `tickets/service.py`.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.x (sync `Session`), Pydantic v2, `pytest` + `pytest-asyncio`. No new dependencies.

## Global Constraints

**Spec requirements binding this phase (copied verbatim from `docs/superpowers/specs/2026-08-24-agentic-helpdesk-design.md`):**

- **§18 phase-5 gate:** "A conversation yields a `tasks` row and a ticket assigned to a specialist whose specialization matches the category."
- **§6.4 Row scoping:** "`list_my_tickets` and `get_ticket` filter on requester identity for employees, on assignee for helpdesk, and are unrestricted for admin. There is no code path that returns a ticket the principal does not own, is not assigned to, or is not admin over."
- **§6.3 Tool authorization:** "A `Deny` produces a `tool_result` with `is_error: true` carrying the reason, an `audit_log` row, and a `denied` span. The loop continues — a denial is information for the agent, not a crash."
- **§14 API surface (Tickets):** "`GET /api/tickets` · `GET /api/tickets/{id}` · `PATCH /api/tickets/{id}` (helpdesk/admin) · `POST /api/tickets/{id}/resolve`"
- **§14:** "Admin endpoints are protected by role check plus an audit-log write on every mutating call."
- **§5.4 `audit_log`:** "append-only; no update or delete path exists in code."
- **§8.4 Specialist routing:** three signals — semantic match against the `helpdesk` collection, live load (open + in-progress ticket count, penalised), and escalation fit (`severity` in `high|critical` or `category == security_incident` requires `escalation_authority == high`; "candidates lacking it are filtered out entirely rather than down-ranked"). "Shift is returned as informational metadata and does not affect scoring." "The rationale string is stored on the ticket so the assignment is explainable in the dossier."
- **§5.3 `tasks.resolution_path`:** enum(`answered`,`ticketed`,`escalated`,`pending`).
- **§5.3 `tickets`:** carries both `assignee_helpdesk_ref` text (`HD-xxx`) **and** `assignee_user_id` FK nullable.
- **§8.3 `create_ticket` gate:** "None, but validated: the task must exist, belong to this conversation, and not already have a ticket." (Note what this does *not* say: the assignee ref is **not** in the validation list — see Task 4.)

**Decisions taken before planning (confirmed with the user — do not relitigate):**

1. **Routing moves to `app/tickets/routing.py`**, per spec §16's repository layout. `agent/tools/routing.py` stays as the thin tool wrapper.
2. **Audit is built this phase**: a helper module, writes on ticket mutations, *and* closing the §6.3 RBAC-deny gap inherited from Phase 4.
3. **The gate is verified twice**: a deterministic scripted-client test in the default suite, plus one `@pytest.mark.live_api` test run once by the controller. Phase 4's live run caught a blocking bug no review found; the model genuinely choosing a matching specialist is the part a scripted client cannot prove.

**Explicitly OUT of scope for this phase (do not build):**

- Any frontend, including the "Tickets board by status" screen — that is spec §15's admin panel, **Phase 8**. There is no `frontend/` directory in this repo yet; do not create one.
- Notifications on ticket status change (§10) — **Phase 6** owns the notifications module and its SSE broker.
- Approval `decide`/`execute`, `executor.py` — **Phase 6**.
- `POST /api/admin/tickets/{id}/dossier` and every other `/api/admin/*` endpoint — **Phase 8**.
- Learning-loop writes on ticket resolution (§13, "Resolution writes an `.md`") — **Phase 9**. Resolving a ticket this phase touches the `tickets` table only.
- `parse_attachment` / multimodal — **Phase 7**.

**Known pre-existing flaky tests — NOT regressions, do not "fix" them as part of a task:**

- `tests/test_eval_retrieval.py::test_retrieval_recall_at_5_meets_accepted_floor` (Chroma test-collection churn).
- `tests/test_security.py::test_tampered_token_is_rejected` (single-char base64 tampering is occasionally a no-op).

If the full suite shows 1–2 failures, check *which* tests failed before treating it as a regression signal.

**Test-fixture gotchas that bit every Phase 4 task from Task 6 onward — read before writing any test:**

- `db_session` (see `backend/tests/conftest.py`) binds a `Session` to a connection inside an outer transaction with `join_transaction_mode="create_savepoint"`, rolled back at teardown. Rows written through it are **invisible to any independently-committing session** (`app.tracing.store` opens its own) under READ COMMITTED. If a test's code path calls `tracing.start_run()` or `check_and_record_usage()` internally and something must see the test's rows across that boundary, the rows must be **hard-committed** through a real `get_sessionmaker()` session and swept in a module-scoped teardown fixture. `tests/test_chat_router.py::_cleanup_sse_test_orphans_after_module` is the worked example — copy its shape, including its `UsageCounter` sweep.
- A `User` row needs `full_name` (NOT NULL) — it is easy to forget and the error is a late flush failure, not a clear one.
- `tests/test_seed.py` asserts exact account counts (`assert total == 126`). A leaked test `User` row breaks it on the *next* suite run, not the current one.
- `audit_log` has **zero foreign-key columns** (`actor_id` and `target_id` are plain `String`), so the cross-connection FK-visibility gap above does not apply to audit rows.

**Conventions:** every new module starts with `from __future__ import annotations`. Sync SQLAlchemy `Session` everywhere except where the call site is inherently async (tool handlers, the agent loop). HTTP endpoints are sync `def` unless they stream. Tool handler functions stay `async def`. Follow the existing import style in each file you touch.

---

### Task 1: Extract specialist routing into `app/tickets/routing.py`

Behaviour-preserving move. Spec §16's layout puts routing at `app/tickets/routing.py`; Phase 4 built it inside `agent/tools/routing.py`. After this task the scoring logic lives in the tickets domain and the agent tool is a thin wrapper — the same shape `tools/tickets.py` → `tickets/service.py` already has. Phase 5's HTTP layer (Task 7) reuses `open_workload()` for reassignment, which is why this comes first.

**Files:**
- Create: `backend/app/tickets/routing.py`
- Modify: `backend/app/agent/tools/routing.py` (becomes a thin wrapper)
- Test: `backend/tests/test_tickets_routing.py` (new)
- Existing test that must keep passing untouched: `backend/tests/test_agent_tools_routing.py`

**Interfaces:**
- Consumes: `app.rag.backend.get_rag_backend()`, `app.db.models.{EscalationAuthority, Role, Ticket, TicketStatus, User}`.
- Produces:
  - `app.tickets.routing.OPEN_STATUSES: tuple[TicketStatus, ...]` — `(OPEN, ASSIGNED, IN_PROGRESS)`
  - `app.tickets.routing.ESCALATING_SEVERITIES: frozenset[str]`
  - `app.tickets.routing.open_workload(db: Session, helpdesk_ref: str) -> int`
  - `app.tickets.routing.workload_by_specialist(db: Session) -> dict[str, int]`
  - `async app.tickets.routing.rank_specialists(db: Session, *, problem_summary: str, category: str, severity: str, limit: int = 3) -> list[dict]` — each dict has keys `helpdesk_ref`, `specialization`, `shift`, `escalation_authority`, `current_workload`, `score`, `rationale`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_tickets_routing.py`:

```python
from __future__ import annotations

import pytest

from app.db.models import EscalationAuthority, Role, Ticket, TicketStatus, User
from app.tickets.routing import OPEN_STATUSES, open_workload, workload_by_specialist


def _make_helpdesk_user(db_session, ref: str, specialization: str, escalation: EscalationAuthority = EscalationAuthority.STANDARD) -> User:
    user = User(
        username=ref.lower(), email=f"{ref.lower()}@northstar.example", full_name=ref, password_hash="x",
        role=Role.HELPDESK, helpdesk_ref=ref, specialization=specialization, escalation_authority=escalation,
    )
    db_session.add(user)
    db_session.commit()
    return user


def test_open_statuses_excludes_terminal_states():
    assert OPEN_STATUSES == (TicketStatus.OPEN, TicketStatus.ASSIGNED, TicketStatus.IN_PROGRESS)
    assert TicketStatus.RESOLVED not in OPEN_STATUSES
    assert TicketStatus.CLOSED not in OPEN_STATUSES


def test_open_workload_counts_only_open_statuses_for_one_ref(db_session, make_ticket):
    make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.OPEN)
    make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.IN_PROGRESS)
    make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.CLOSED)
    make_ticket(assignee_helpdesk_ref="HD-902", status=TicketStatus.OPEN)

    assert open_workload(db_session, "HD-901") == 2


def test_workload_by_specialist_groups_counts_per_ref(db_session, make_ticket):
    make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.OPEN)
    make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.ASSIGNED)
    make_ticket(assignee_helpdesk_ref="HD-902", status=TicketStatus.OPEN)
    make_ticket(assignee_helpdesk_ref="HD-903", status=TicketStatus.RESOLVED)

    counts = workload_by_specialist(db_session)

    assert counts["HD-901"] == 2
    assert counts["HD-902"] == 1
    assert "HD-903" not in counts
```

This uses a `make_ticket` fixture that does not exist yet. Add it to `backend/tests/conftest.py` (append at the end of the file) — every later task in this plan needs it too:

```python
@pytest.fixture()
def make_ticket(db_session):
    """Ticket.task_id and Ticket.conversation_id are NOT NULL foreign keys,
    and Task.classified_by_run_id is one too -- a Ticket cannot be built in
    isolation. Creates the whole Conversation/Run/Task chain through
    db_session (NOT tracing.start_run(), which commits on its own connection
    and would deadlock against db_session's held transaction) so everything
    rolls back together at teardown."""
    import uuid as _uuid

    from app.db.models import (
        Conversation, Run, RunStatus, RunTrigger, Severity, Task, TaskCategory,
        Ticket, TicketPriority, TicketStatus,
    )

    def _make(
        *,
        assignee_helpdesk_ref: str = "HD-901",
        status: TicketStatus = TicketStatus.OPEN,
        priority: TicketPriority = TicketPriority.MEDIUM,
        category: TaskCategory = TaskCategory.VPN_NETWORK,
        requester_user_id: _uuid.UUID | None = None,
        requester_guest_email: str | None = None,
        title: str = "Ticket title",
    ) -> Ticket:
        conv = Conversation(guest_name="Guest", guest_email="guest@example.com")
        db_session.add(conv)
        run = Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK)
        db_session.add(run)
        db_session.commit()

        task = Task(
            conversation_id=conv.id, user_id=None, guest_email="guest@example.com",
            title=title, category=category, severity=Severity.MEDIUM, summary="s",
            affected_systems=[], evidence={}, classified_by_run_id=run.id,
        )
        db_session.add(task)
        db_session.commit()

        ticket = Ticket(
            task_id=task.id, conversation_id=conv.id,
            requester_user_id=requester_user_id, requester_guest_email=requester_guest_email,
            assignee_helpdesk_ref=assignee_helpdesk_ref, matched_specialization="Network and VPN Support",
            assignment_rationale="seeded by make_ticket", assignment_score=0.9,
            priority=priority, status=status, title=title, body="Body",
        )
        db_session.add(ticket)
        db_session.commit()
        db_session.refresh(ticket)
        return ticket

    return _make
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_tickets_routing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.tickets.routing'`

- [ ] **Step 3: Create `app/tickets/routing.py` by moving the logic out of the tool module**

Create `backend/app/tickets/routing.py`:

```python
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import EscalationAuthority, Role, Ticket, TicketStatus, User
from app.rag.backend import get_rag_backend

ESCALATING_SEVERITIES = frozenset({"high", "critical"})
OPEN_STATUSES = (TicketStatus.OPEN, TicketStatus.ASSIGNED, TicketStatus.IN_PROGRESS)

# A concrete, monotonic scoring formula (spec 8.4's three signals): semantic
# rank dominates (the 25 specializations are distinct/single-holder, so
# rank position is the strongest signal), workload penalizes a busy
# specialist without ever overriding a clearly-better semantic match or the
# escalation-authority hard filter, which runs before scoring, not as a
# score component.
SEMANTIC_RANK_PENALTY = 0.15
WORKLOAD_PENALTY = 0.05


def collapse_to_unique_helpdesk_ids(query_result) -> list[str]:
    """Chunks are ranked by ascending distance; keep first-seen (best) order
    per unique helpdesk_id, discarding later, worse-ranked chunks of a
    document already seen -- the same document-collapsing principle
    scripts/eval_retrieval.py uses for the qrels-comparable Recall metrics."""
    seen: list[str] = []
    for metadata in query_result["metadatas"]:
        helpdesk_id = metadata.get("helpdesk_id")
        if helpdesk_id and helpdesk_id not in seen:
            seen.append(helpdesk_id)
    return seen


def open_workload(db: Session, helpdesk_ref: str) -> int:
    """Spec 8.4's 'live load' signal: open + in-progress tickets for one
    specialist. Also used by the reassignment endpoint (tickets/router.py)."""
    return db.query(Ticket).filter(
        Ticket.assignee_helpdesk_ref == helpdesk_ref,
        Ticket.status.in_(OPEN_STATUSES),
    ).count()


def workload_by_specialist(db: Session) -> dict[str, int]:
    rows = db.query(Ticket.assignee_helpdesk_ref).filter(Ticket.status.in_(OPEN_STATUSES)).all()
    counts: dict[str, int] = {}
    for (ref,) in rows:
        counts[ref] = counts.get(ref, 0) + 1
    return counts


async def rank_specialists(
    db: Session, *, problem_summary: str, category: str, severity: str, limit: int = 3,
) -> list[dict]:
    """Spec 8.4: combine semantic match, live load, and escalation fit, and
    return the top `limit` candidates with a score breakdown. Escalation fit
    is a hard filter applied before scoring -- candidates lacking `high`
    escalation authority for a high/critical severity or a security_incident
    are removed entirely, never merely down-ranked. Shift is returned as
    informational metadata and does not affect the score."""
    backend = get_rag_backend()
    result = await backend.query("helpdesk", problem_summary, where={}, k=15)
    ranked_ids = collapse_to_unique_helpdesk_ids(result)

    candidates: list[dict] = []
    for rank, helpdesk_ref in enumerate(ranked_ids):
        user = db.query(User).filter(User.role == Role.HELPDESK, User.helpdesk_ref == helpdesk_ref).one_or_none()
        if user is None:
            continue
        if severity in ESCALATING_SEVERITIES or category == "security_incident":
            if user.escalation_authority != EscalationAuthority.HIGH:
                continue
        workload = open_workload(db, helpdesk_ref)
        score = max(0.0, min(1.0, 1.0 - (rank * SEMANTIC_RANK_PENALTY) - (workload * WORKLOAD_PENALTY)))
        candidates.append({
            "helpdesk_ref": helpdesk_ref,
            "specialization": user.specialization,
            "shift": user.shift,
            "escalation_authority": user.escalation_authority.value if user.escalation_authority else None,
            "current_workload": workload,
            "score": round(score, 4),
            "rationale": f"Semantic match rank {rank + 1}; current workload: {workload} open ticket(s).",
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:limit]
```

- [ ] **Step 4: Reduce `agent/tools/routing.py` to a thin wrapper**

Replace the entire contents of `backend/app/agent/tools/routing.py` with:

```python
from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.rbac.policy import Principal
from app.tickets.routing import open_workload, rank_specialists, workload_by_specialist


class FindHelpdeskSpecialistArgs(BaseModel):
    problem_summary: str
    category: str
    severity: str = "medium"


class GetHelpdeskWorkloadArgs(BaseModel):
    helpdesk_ref: str | None = None


async def find_helpdesk_specialist_handler(principal: Principal, db: Session, args: FindHelpdeskSpecialistArgs) -> dict:
    candidates = await rank_specialists(
        db, problem_summary=args.problem_summary, category=args.category, severity=args.severity,
    )
    return {"candidates": candidates}


async def get_helpdesk_workload_handler(principal: Principal, db: Session, args: GetHelpdeskWorkloadArgs) -> dict:
    if args.helpdesk_ref is not None:
        return {"helpdesk_ref": args.helpdesk_ref, "open_and_in_progress": open_workload(db, args.helpdesk_ref)}
    return {"workload_by_specialist": workload_by_specialist(db)}
```

- [ ] **Step 5: Run both routing test files — the new one AND Phase 4's, unchanged**

Run: `cd backend && uv run pytest tests/test_tickets_routing.py tests/test_agent_tools_routing.py tests/test_agent_registry.py -v`
Expected: PASS. `test_agent_tools_routing.py` must pass **with no edits** — that is the proof this move was behaviour-preserving. If it needed edits, the move changed behaviour; revert and redo.

- [ ] **Step 6: Commit**

```bash
git add backend/app/tickets/routing.py backend/app/agent/tools/routing.py backend/tests/test_tickets_routing.py backend/tests/conftest.py
git commit -m "Extract specialist routing into app/tickets/routing.py per spec section 16 layout"
```

---

### Task 2: Append-only audit log service

`audit_log` has existed as a table since Phase 1 and **nothing in the codebase has ever written to it**. This task builds the single write path. Spec §5.4: "append-only; no update or delete path exists in code" — so this module exposes exactly one function, and no update or delete helper is added anywhere.

The function **adds and flushes but does not commit**, so an audit row lands in the same transaction as the change it describes. That is deliberate: an audit row must never describe a mutation that was subsequently rolled back. Callers commit. (This differs from `app/tracing/store.py`, which commits independently *because* a trace must survive a failed business transaction — the opposite requirement.)

**Files:**
- Create: `backend/app/audit/__init__.py` (empty)
- Create: `backend/app/audit/service.py`
- Test: `backend/tests/test_audit_service.py` (new)

Note on placement: spec §16's layout does not enumerate an `audit/` module, but it also does not enumerate `chat/`, which Phase 4 created and which was accepted. A dedicated module keeps the single write path obvious.

**Interfaces:**
- Consumes: `app.db.models.{ActorType, AuditLog}`, `app.rbac.policy.Principal`.
- Produces:
  - `app.audit.service.record_audit(db: Session, *, actor_type: ActorType, actor_id: str | None, action: str, target_type: str, target_id: str, payload: dict | None = None, ip_address: str | None = None) -> AuditLog`
  - `app.audit.service.actor_from_principal(principal: Principal) -> tuple[ActorType, str | None]`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_audit_service.py`:

```python
from __future__ import annotations

import inspect

import app.audit.service as audit_service
from app.audit.service import actor_from_principal, record_audit
from app.db.models import ActorType, AuditLog
from app.rbac.policy import Principal

_EMPLOYEE = Principal(kind="user", user_id="00000000-0000-0000-0000-000000000009", role="employee", clearance="standard", department="Engineering", employee_ref="EMP-009", helpdesk_ref=None)
_GUEST = Principal(kind="guest", user_id=None, role="guest", clearance=None, department=None, employee_ref=None, helpdesk_ref=None, guest_name="G", guest_email="g@example.com")


def test_record_audit_writes_a_row_with_every_field(db_session):
    row = record_audit(
        db_session, actor_type=ActorType.USER, actor_id="EMP-009", action="ticket.resolve",
        target_type="ticket", target_id="TCK-000001", payload={"resolution": "done"}, ip_address="10.0.0.1",
    )
    db_session.commit()

    stored = db_session.get(AuditLog, row.id)
    assert stored.actor_type == ActorType.USER
    assert stored.actor_id == "EMP-009"
    assert stored.action == "ticket.resolve"
    assert stored.target_type == "ticket"
    assert stored.target_id == "TCK-000001"
    assert stored.payload == {"resolution": "done"}
    assert stored.ip_address == "10.0.0.1"
    assert stored.created_at is not None


def test_record_audit_does_not_commit_so_the_row_shares_the_callers_transaction(db_session):
    """An audit row must never describe a mutation that was rolled back --
    so record_audit stages the row and leaves the commit to the caller."""
    record_audit(
        db_session, actor_type=ActorType.AGENT, actor_id=None, action="tool.denied",
        target_type="tool", target_id="search_knowledge",
    )
    db_session.rollback()

    assert db_session.query(AuditLog).filter(AuditLog.action == "tool.denied").count() == 0


def test_record_audit_defaults_payload_to_empty_dict(db_session):
    row = record_audit(
        db_session, actor_type=ActorType.SYSTEM, actor_id=None, action="x",
        target_type="t", target_id="1",
    )
    db_session.commit()
    assert db_session.get(AuditLog, row.id).payload == {}


def test_actor_from_principal_maps_user_and_guest():
    assert actor_from_principal(_EMPLOYEE) == (ActorType.USER, "00000000-0000-0000-0000-000000000009")
    assert actor_from_principal(_GUEST) == (ActorType.USER, "g@example.com")


def test_audit_module_exposes_no_update_or_delete_path():
    """Spec 5.4: audit_log is append-only, 'no update or delete path exists
    in code'. This asserts the module's public surface stays write-only --
    it fails the moment someone adds a delete_audit/purge/update helper."""
    public = {name for name, obj in inspect.getmembers(audit_service, inspect.isfunction) if not name.startswith("_") and obj.__module__ == audit_service.__name__}
    assert public == {"record_audit", "actor_from_principal"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_audit_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.audit'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/audit/__init__.py` as an empty file.

Create `backend/app/audit/service.py`:

```python
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import ActorType, AuditLog
from app.rbac.policy import Principal


def actor_from_principal(principal: Principal) -> tuple[ActorType, str | None]:
    """A guest is still a human actor (ActorType.USER), identified by the
    contact email their JWT carries -- guests are deliberately not rows in
    `users` (spec 5.1), so there is no id to record. ActorType.AGENT is for
    actions the model takes on its own behalf; ActorType.SYSTEM is for
    unattended jobs."""
    if principal.kind == "guest":
        return ActorType.USER, principal.guest_email
    return ActorType.USER, principal.user_id


def record_audit(
    db: Session,
    *,
    actor_type: ActorType,
    actor_id: str | None,
    action: str,
    target_type: str,
    target_id: str,
    payload: dict | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """The ONLY write path to `audit_log`, which spec 5.4 defines as
    append-only: no update or delete helper exists in this module and none
    may be added.

    Stages the row and flushes (so `.id` is populated) but deliberately does
    NOT commit -- the row belongs to the caller's transaction, so an audit
    entry can never survive a mutation that was rolled back. This is the
    opposite of app/tracing/store.py, which commits on its own connection
    precisely so a trace outlives a failed business transaction.
    """
    row = AuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload=payload if payload is not None else {},
        ip_address=ip_address,
    )
    db.add(row)
    db.flush()
    return row
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_audit_service.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/audit/ backend/tests/test_audit_service.py
git commit -m "Add append-only audit_log write path (app/audit/service.py)"
```

---

### Task 3: Close spec §6.3's RBAC-denial audit gap

Spec §6.3 has required since Phase 4 that a `Deny` produces "a `tool_result` with `is_error: true` carrying the reason, an `audit_log` row, and a `denied` span." Phase 4 shipped the `is_error` result and the span; the `audit_log` row was never written. Now that Task 2 exists, close it.

**Files:**
- Modify: `backend/app/agent/registry.py` (the `Deny` branch of `dispatch_tool`)
- Test: `backend/tests/test_agent_registry.py` (add one test)

**Interfaces:**
- Consumes: `app.audit.service.{record_audit, actor_from_principal}` (Task 2), `app.db.models.ActorType`.
- Produces: nothing new — an existing code path gains a side effect.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_agent_registry.py`, immediately after `test_dispatch_tool_denies_guest_for_search_knowledge`:

```python
async def test_dispatch_tool_writes_an_audit_row_when_authorize_denies(db_session):
    """Spec 6.3: a Deny produces an is_error tool_result, an audit_log row,
    AND a denied span. Phase 4 shipped the first and third; this covers the
    audit row. dispatch_tool must commit it -- nothing else will, since a
    denial short-circuits before any handler (and therefore any commit) runs."""
    from app.db.models import ActorType, AuditLog

    result = await dispatch_tool(
        _GUEST, db=db_session, tool_name="search_knowledge", tool_use_id="t1",
        raw_input='{"query": "x"}', extra_context={},
    )
    assert result["is_error"] is True

    row = db_session.query(AuditLog).filter(
        AuditLog.action == "tool.denied", AuditLog.target_id == "search_knowledge",
    ).one()
    assert row.actor_type == ActorType.USER
    assert row.target_type == "tool"
    assert "guests cannot use" in row.payload["reason"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_agent_registry.py::test_dispatch_tool_writes_an_audit_row_when_authorize_denies -v`
Expected: FAIL with `sqlalchemy.exc.NoResultFound: No row was found when one was required`

- [ ] **Step 3: Write the audit row in the Deny branch**

In `backend/app/agent/registry.py`, add to the imports at the top of the file:

```python
from app.audit.service import actor_from_principal, record_audit
from app.db.models import ActorType, SpanKind
```

(the existing line is `from app.db.models import SpanKind` — extend it rather than adding a second import from the same module).

Then replace the `Deny` branch:

```python
    if isinstance(decision, Deny):
        return {"is_error": True, "content": decision.reason}
```

with:

```python
    if isinstance(decision, Deny):
        # Spec 6.3: a denial produces an is_error tool_result, an audit_log
        # row, and a denied span -- and the loop continues. record_audit
        # deliberately does not commit, so commit here: a denial
        # short-circuits before any handler runs, so nothing else in this
        # turn will commit on our behalf, and there is no partial handler
        # state that this commit could prematurely persist.
        actor_type, actor_id = actor_from_principal(principal)
        record_audit(
            db, actor_type=actor_type, actor_id=actor_id, action="tool.denied",
            target_type="tool", target_id=tool_name, payload={"reason": decision.reason},
        )
        db.commit()
        return {"is_error": True, "content": decision.reason}
```

Note `dispatch_tool` is called with `db=None` by one existing test (`test_dispatch_tool_denies_guest_for_search_knowledge`). Update that call to pass `db=db_session` and give the test the `db_session` fixture:

```python
async def test_dispatch_tool_denies_guest_for_search_knowledge(db_session):
    result = await dispatch_tool(_GUEST, db=db_session, tool_name="search_knowledge", tool_use_id="t1", raw_input='{"query": "x"}', extra_context={})
    assert result["is_error"] is True
```

- [ ] **Step 4: Run the registry tests**

Run: `cd backend && uv run pytest tests/test_agent_registry.py tests/test_rbac_authorize.py -v`
Expected: PASS (all of them, including the two edited)

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent/registry.py backend/tests/test_agent_registry.py
git commit -m "Write an audit_log row on RBAC tool denial, closing the spec section 6.3 gap"
```

---

### Task 4: Ticket creation completeness — `assignee_user_id` and `resolution_path`

Two spec columns that Phase 4's `create_ticket` never populates: `tickets.assignee_user_id` (§5.3) stays NULL, and the task's `resolution_path` stays `pending` forever even after a ticket exists, when §5.3's enum has `ticketed` for exactly this. Both are needed by the phase gate ("a ticket assigned to a specialist") and by Phase 8's dossier.

On an unknown `assignee_helpdesk_ref`: leave `assignee_user_id` NULL rather than raising. Spec §8.3 lists `create_ticket`'s validations exhaustively — "the task must exist, belong to this conversation, and not already have a ticket" — and the assignee ref is deliberately not among them. The text ref is the source of truth; the FK is a convenience join that is populated when it can be resolved.

**Files:**
- Modify: `backend/app/tickets/service.py` (`create_ticket`)
- Test: `backend/tests/test_tickets_service.py` (add three tests)

**Interfaces:**
- Consumes: `app.db.models.{ResolutionPath, Role, User}`.
- Produces: `create_ticket()` keeps its exact signature and return type; it now additionally sets `Ticket.assignee_user_id` when the ref resolves, and flips the parent `Task.resolution_path` to `ResolutionPath.TICKETED`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_tickets_service.py` (the file already imports `create_ticket`, `record_task`, `TaskCategory`, `TicketPriority` and defines `_make_conversation`/`_make_run`):

```python
def test_create_ticket_resolves_assignee_user_id_from_the_helpdesk_ref(db_session):
    from app.db.models import EscalationAuthority, Role, User

    specialist = User(
        username="hd-950", email="hd-950@northstar.example", full_name="HD-950", password_hash="x",
        role=Role.HELPDESK, helpdesk_ref="HD-950", specialization="Network and VPN Support",
        escalation_authority=EscalationAuthority.STANDARD,
    )
    db_session.add(specialist)
    db_session.commit()

    conv = _make_conversation(db_session)
    run_id = _make_run(db_session)
    task = record_task(
        db_session, conversation_id=conv.id, user_id=None, guest_email=conv.guest_email,
        title="VPN issue", category=TaskCategory.VPN_NETWORK, severity="medium",
        summary="s", affected_systems=[], evidence={}, classified_by_run_id=run_id,
    )

    ticket = create_ticket(
        db_session, task_id=task.id, conversation_id=conv.id, requester_user_id=None,
        requester_guest_email=conv.guest_email, assignee_helpdesk_ref="HD-950",
        priority=TicketPriority.MEDIUM, title="VPN issue", body="Full description",
        assignment_rationale="Best semantic match.", matched_specialization="Network and VPN Support",
        assignment_score=0.87,
    )

    assert ticket.assignee_user_id == specialist.id


def test_create_ticket_leaves_assignee_user_id_null_for_an_unknown_ref(db_session):
    """Spec 8.3 lists create_ticket's validations exhaustively and the
    assignee ref is deliberately not among them -- the text ref stays the
    source of truth, the FK is a convenience join populated when resolvable."""
    conv = _make_conversation(db_session)
    run_id = _make_run(db_session)
    task = record_task(
        db_session, conversation_id=conv.id, user_id=None, guest_email=conv.guest_email,
        title="VPN issue", category=TaskCategory.VPN_NETWORK, severity="medium",
        summary="s", affected_systems=[], evidence={}, classified_by_run_id=run_id,
    )

    ticket = create_ticket(
        db_session, task_id=task.id, conversation_id=conv.id, requester_user_id=None,
        requester_guest_email=conv.guest_email, assignee_helpdesk_ref="HD-DOES-NOT-EXIST",
        priority=TicketPriority.MEDIUM, title="VPN issue", body="Full description",
        assignment_rationale="r", matched_specialization="s", assignment_score=0.5,
    )

    assert ticket.assignee_user_id is None
    assert ticket.assignee_helpdesk_ref == "HD-DOES-NOT-EXIST"


def test_create_ticket_flips_the_task_resolution_path_to_ticketed(db_session):
    from app.db.models import ResolutionPath

    conv = _make_conversation(db_session)
    run_id = _make_run(db_session)
    task = record_task(
        db_session, conversation_id=conv.id, user_id=None, guest_email=conv.guest_email,
        title="VPN issue", category=TaskCategory.VPN_NETWORK, severity="medium",
        summary="s", affected_systems=[], evidence={}, classified_by_run_id=run_id,
    )
    assert task.resolution_path == ResolutionPath.PENDING

    create_ticket(
        db_session, task_id=task.id, conversation_id=conv.id, requester_user_id=None,
        requester_guest_email=conv.guest_email, assignee_helpdesk_ref="HD-950",
        priority=TicketPriority.MEDIUM, title="VPN issue", body="Full description",
        assignment_rationale="r", matched_specialization="s", assignment_score=0.5,
    )

    db_session.refresh(task)
    assert task.resolution_path == ResolutionPath.TICKETED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_tickets_service.py -v -k "assignee_user_id or resolution_path_to_ticketed"`
Expected: FAIL — `assert None == UUID(...)` on the first, and `assert ResolutionPath.PENDING == ResolutionPath.TICKETED` on the third.

- [ ] **Step 3: Populate both columns in `create_ticket`**

In `backend/app/tickets/service.py`, extend the model import:

```python
from app.db.models import ResolutionPath, Role, Severity, Task, TaskCategory, Ticket, TicketPriority, User
```

Then in `create_ticket`, replace the `ticket = Ticket(...)` / `db.add` / `db.commit` / `db.refresh` block with:

```python
    # Spec 5.3 carries both an assignee_helpdesk_ref text column and an
    # assignee_user_id FK. The ref is the source of truth (it is what
    # routing produced); the FK is a convenience join, populated when it
    # resolves. An unresolvable ref is NOT an error: spec 8.3 lists this
    # function's validations exhaustively and the assignee is not among them.
    assignee = db.query(User).filter(
        User.role == Role.HELPDESK, User.helpdesk_ref == assignee_helpdesk_ref,
    ).one_or_none()

    ticket = Ticket(
        task_id=task_id,
        conversation_id=conversation_id,
        requester_user_id=requester_user_id,
        requester_guest_email=requester_guest_email,
        assignee_helpdesk_ref=assignee_helpdesk_ref,
        assignee_user_id=assignee.id if assignee is not None else None,
        matched_specialization=matched_specialization,
        assignment_rationale=assignment_rationale,
        assignment_score=assignment_score,
        priority=TicketPriority(priority) if not isinstance(priority, TicketPriority) else priority,
        title=title,
        body=body,
    )
    db.add(ticket)
    # The task is no longer merely classified -- it has become a ticket
    # (spec 5.3's resolution_path enum). Same transaction as the insert:
    # a task must never read `ticketed` without its ticket existing.
    task.resolution_path = ResolutionPath.TICKETED
    db.commit()
    db.refresh(ticket)
    return ticket
```

- [ ] **Step 4: Run the tickets service tests**

Run: `cd backend && uv run pytest tests/test_tickets_service.py tests/test_agent_tools_tickets.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add backend/app/tickets/service.py backend/tests/test_tickets_service.py
git commit -m "Populate assignee_user_id and flip task resolution_path to ticketed on ticket creation"
```

---

### Task 5: Status transitions and the resolve operation

The lifecycle service. `tickets.status` has six values and until now only ever holds `open`. This adds a validated state machine plus the two mutations Phase 5's endpoints need.

**Files:**
- Modify: `backend/app/tickets/service.py`
- Test: `backend/tests/test_tickets_lifecycle.py` (new)

**Interfaces:**
- Consumes: `app.db.models.{Ticket, TicketStatus}`, `datetime`.
- Produces:
  - `app.tickets.service.LEGAL_TRANSITIONS: dict[TicketStatus, frozenset[TicketStatus]]`
  - `app.tickets.service.InvalidTransition(ValueError)`
  - `app.tickets.service.transition_status(db, ticket: Ticket, new_status: TicketStatus | str) -> Ticket`
  - `app.tickets.service.reassign(db, ticket: Ticket, *, assignee_helpdesk_ref: str, rationale: str) -> Ticket`
  - `app.tickets.service.resolve_ticket(db, ticket: Ticket, *, resolution: str, resolved_by_user_id: uuid.UUID | None) -> Ticket`

All three **stage** changes and do not commit — the endpoints (Tasks 7 and 8) commit the mutation and its audit row together.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_tickets_lifecycle.py`:

```python
from __future__ import annotations

import uuid

import pytest

from app.db.models import TicketStatus
from app.tickets.service import (
    LEGAL_TRANSITIONS, InvalidTransition, reassign, resolve_ticket, transition_status,
)


def test_closed_is_terminal():
    assert LEGAL_TRANSITIONS[TicketStatus.CLOSED] == frozenset()


def test_every_status_has_an_entry_so_a_new_enum_value_cannot_be_forgotten():
    assert set(LEGAL_TRANSITIONS) == set(TicketStatus)


@pytest.mark.parametrize("start,target", [
    (TicketStatus.OPEN, TicketStatus.ASSIGNED),
    (TicketStatus.OPEN, TicketStatus.IN_PROGRESS),
    (TicketStatus.ASSIGNED, TicketStatus.IN_PROGRESS),
    (TicketStatus.IN_PROGRESS, TicketStatus.RESOLVED),
    (TicketStatus.IN_PROGRESS, TicketStatus.ESCALATED),
    (TicketStatus.ESCALATED, TicketStatus.RESOLVED),
    (TicketStatus.RESOLVED, TicketStatus.CLOSED),
    (TicketStatus.RESOLVED, TicketStatus.IN_PROGRESS),
])
def test_legal_transitions_are_applied(db_session, make_ticket, start, target):
    ticket = make_ticket(status=start)
    transition_status(db_session, ticket, target)
    db_session.commit()
    assert ticket.status == target


@pytest.mark.parametrize("start,target", [
    (TicketStatus.CLOSED, TicketStatus.OPEN),
    (TicketStatus.CLOSED, TicketStatus.IN_PROGRESS),
    (TicketStatus.OPEN, TicketStatus.RESOLVED),
    (TicketStatus.RESOLVED, TicketStatus.ASSIGNED),
])
def test_illegal_transitions_raise_and_leave_the_row_untouched(db_session, make_ticket, start, target):
    ticket = make_ticket(status=start)

    with pytest.raises(InvalidTransition):
        transition_status(db_session, ticket, target)

    assert ticket.status == start


def test_transition_status_accepts_a_raw_string(db_session, make_ticket):
    ticket = make_ticket(status=TicketStatus.OPEN)
    transition_status(db_session, ticket, "in_progress")
    db_session.commit()
    assert ticket.status == TicketStatus.IN_PROGRESS


def test_transition_status_rejects_an_unknown_status_string(db_session, make_ticket):
    ticket = make_ticket(status=TicketStatus.OPEN)
    with pytest.raises(ValueError):
        transition_status(db_session, ticket, "not-a-status")


def test_resolve_ticket_sets_resolution_fields_and_status(db_session, make_ticket):
    ticket = make_ticket(status=TicketStatus.IN_PROGRESS)

    resolve_ticket(db_session, ticket, resolution="Reset the VPN profile.", resolved_by_user_id=None)
    db_session.commit()

    assert ticket.status == TicketStatus.RESOLVED
    assert ticket.resolution == "Reset the VPN profile."
    assert ticket.resolved_at is not None


def test_resolve_ticket_records_who_resolved_it(db_session, make_ticket):
    from app.db.models import EscalationAuthority, Role, User

    resolver = User(
        username="hd-960", email="hd-960@northstar.example", full_name="HD-960", password_hash="x",
        role=Role.HELPDESK, helpdesk_ref="HD-960", specialization="Network and VPN Support",
        escalation_authority=EscalationAuthority.STANDARD,
    )
    db_session.add(resolver)
    db_session.commit()

    ticket = make_ticket(status=TicketStatus.IN_PROGRESS)
    resolve_ticket(db_session, ticket, resolution="Done.", resolved_by_user_id=resolver.id)
    db_session.commit()

    assert ticket.resolved_by_user_id == resolver.id


def test_resolve_ticket_refuses_an_illegal_source_status(db_session, make_ticket):
    ticket = make_ticket(status=TicketStatus.CLOSED)
    with pytest.raises(InvalidTransition):
        resolve_ticket(db_session, ticket, resolution="too late", resolved_by_user_id=None)


def test_resolve_ticket_requires_a_non_empty_resolution(db_session, make_ticket):
    ticket = make_ticket(status=TicketStatus.IN_PROGRESS)
    with pytest.raises(ValueError):
        resolve_ticket(db_session, ticket, resolution="   ", resolved_by_user_id=None)


def test_reassign_updates_ref_user_id_and_rationale(db_session, make_ticket):
    from app.db.models import EscalationAuthority, Role, User

    new_owner = User(
        username="hd-970", email="hd-970@northstar.example", full_name="HD-970", password_hash="x",
        role=Role.HELPDESK, helpdesk_ref="HD-970", specialization="Identity and Access Management",
        escalation_authority=EscalationAuthority.HIGH,
    )
    db_session.add(new_owner)
    db_session.commit()

    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.OPEN)
    reassign(db_session, ticket, assignee_helpdesk_ref="HD-970", rationale="Escalation authority required.")
    db_session.commit()

    assert ticket.assignee_helpdesk_ref == "HD-970"
    assert ticket.assignee_user_id == new_owner.id
    assert "Escalation authority required." in ticket.assignment_rationale


def test_reassign_to_an_unknown_ref_nulls_the_fk_but_keeps_the_ref(db_session, make_ticket):
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.OPEN)
    reassign(db_session, ticket, assignee_helpdesk_ref="HD-NOPE", rationale="manual override")
    db_session.commit()

    assert ticket.assignee_helpdesk_ref == "HD-NOPE"
    assert ticket.assignee_user_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_tickets_lifecycle.py -v`
Expected: FAIL with `ImportError: cannot import name 'LEGAL_TRANSITIONS' from 'app.tickets.service'`

- [ ] **Step 3: Write the implementation**

Append to `backend/app/tickets/service.py`. First extend the imports at the top of the file:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import (
    ResolutionPath, Role, Severity, Task, TaskCategory, Ticket, TicketPriority, TicketStatus, User,
)
```

Then append at the end of the file:

```python
class InvalidTransition(ValueError):
    """Raised when a caller asks for a status change the lifecycle forbids."""


# Spec 5.3's six statuses as an explicit state machine. Read as
# "from -> the set of statuses reachable from it". CLOSED is terminal;
# RESOLVED can be reopened to IN_PROGRESS because a resolution that did not
# hold is a normal helpdesk outcome, not a data-repair scenario. Every
# TicketStatus must appear as a key -- a test asserts this, so adding a new
# status to the enum cannot silently leave a hole here.
LEGAL_TRANSITIONS: dict[TicketStatus, frozenset[TicketStatus]] = {
    TicketStatus.OPEN: frozenset({
        TicketStatus.ASSIGNED, TicketStatus.IN_PROGRESS, TicketStatus.ESCALATED, TicketStatus.CLOSED,
    }),
    TicketStatus.ASSIGNED: frozenset({
        TicketStatus.IN_PROGRESS, TicketStatus.ESCALATED, TicketStatus.RESOLVED, TicketStatus.CLOSED,
    }),
    TicketStatus.IN_PROGRESS: frozenset({
        TicketStatus.RESOLVED, TicketStatus.ESCALATED, TicketStatus.CLOSED,
    }),
    TicketStatus.ESCALATED: frozenset({
        TicketStatus.IN_PROGRESS, TicketStatus.RESOLVED, TicketStatus.CLOSED,
    }),
    TicketStatus.RESOLVED: frozenset({TicketStatus.CLOSED, TicketStatus.IN_PROGRESS}),
    TicketStatus.CLOSED: frozenset(),
}


def transition_status(db: Session, ticket: Ticket, new_status: TicketStatus | str) -> Ticket:
    """Stages a validated status change. Does NOT commit -- callers commit
    the change together with its audit_log row so the two can never
    disagree. Raises InvalidTransition for a forbidden move and ValueError
    for a status string that is not a TicketStatus at all."""
    target = TicketStatus(new_status) if not isinstance(new_status, TicketStatus) else new_status
    if target not in LEGAL_TRANSITIONS[ticket.status]:
        raise InvalidTransition(
            f"cannot move ticket from {ticket.status.value!r} to {target.value!r}"
        )
    ticket.status = target
    return ticket


def reassign(db: Session, ticket: Ticket, *, assignee_helpdesk_ref: str, rationale: str) -> Ticket:
    """Stages a reassignment, appending to (never overwriting) the rationale
    so the assignment history stays explainable in the dossier (spec 8.4:
    'the rationale string is stored on the ticket so the assignment is
    explainable'). Mirrors create_ticket's rule for an unresolvable ref:
    the text ref is authoritative, the FK is nulled rather than rejected."""
    assignee = db.query(User).filter(
        User.role == Role.HELPDESK, User.helpdesk_ref == assignee_helpdesk_ref,
    ).one_or_none()
    previous = ticket.assignee_helpdesk_ref
    ticket.assignee_helpdesk_ref = assignee_helpdesk_ref
    ticket.assignee_user_id = assignee.id if assignee is not None else None
    if assignee is not None and assignee.specialization:
        ticket.matched_specialization = assignee.specialization
    ticket.assignment_rationale = (
        f"{ticket.assignment_rationale}\nReassigned from {previous} to {assignee_helpdesk_ref}: {rationale}"
    )
    return ticket


def resolve_ticket(
    db: Session, ticket: Ticket, *, resolution: str, resolved_by_user_id: uuid.UUID | None,
) -> Ticket:
    """Stages the resolve transition plus its three resolution columns
    (spec 5.3). Does NOT commit. A blank resolution is rejected -- the
    resolution text is what Phase 9's learning loop later reads, so an
    empty one is worse than no resolution at all."""
    if not resolution or not resolution.strip():
        raise ValueError("resolution must not be empty")
    transition_status(db, ticket, TicketStatus.RESOLVED)
    ticket.resolution = resolution.strip()
    ticket.resolved_by_user_id = resolved_by_user_id
    ticket.resolved_at = datetime.now(timezone.utc)
    return ticket
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_tickets_lifecycle.py -v`
Expected: PASS (all parametrized cases included)

- [ ] **Step 5: Commit**

```bash
git add backend/app/tickets/service.py backend/tests/test_tickets_lifecycle.py
git commit -m "Add ticket status state machine, reassign, and resolve to the tickets service"
```

---

### Task 6: Row scoping (spec §6.4) as one shared, tested chokepoint

Spec §6.4 is absolute: "There is no code path that returns a ticket the principal does not own, is not assigned to, or is not admin over." Today `list_my_tickets_handler` filters only on requester identity — so a **helpdesk user cannot see the tickets assigned to them** and an **admin sees only their own**, both contradicting §6.4. Rather than fix that in place and write a second, subtly different rule for the HTTP layer, build one function and route every caller through it.

**Files:**
- Create: `backend/app/tickets/scoping.py`
- Modify: `backend/app/agent/tools/tickets.py` (`_row_scope_filter`, `list_my_tickets_handler`, `get_ticket_handler`)
- Test: `backend/tests/test_tickets_scoping.py` (new)

**Interfaces:**
- Consumes: `app.db.models.Ticket`, `app.rbac.policy.Principal`.
- Produces:
  - `app.tickets.scoping.scope_tickets_query(query, principal: Principal, *, guest_email: str | None = None)`
  - `app.tickets.scoping.can_read_ticket(principal: Principal, ticket: Ticket, *, guest_email: str | None = None) -> bool`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_tickets_scoping.py`:

```python
from __future__ import annotations

import uuid

from app.db.models import Ticket, TicketStatus
from app.rbac.policy import Principal
from app.tickets.scoping import can_read_ticket, scope_tickets_query

_REQUESTER_ID = uuid.uuid4()
_OTHER_ID = uuid.uuid4()

_EMPLOYEE = Principal(kind="user", user_id=str(_REQUESTER_ID), role="employee", clearance="standard", department="Engineering", employee_ref="EMP-001", helpdesk_ref=None)
_OTHER_EMPLOYEE = Principal(kind="user", user_id=str(_OTHER_ID), role="employee", clearance="standard", department="Engineering", employee_ref="EMP-002", helpdesk_ref=None)
_HELPDESK = Principal(kind="user", user_id=str(uuid.uuid4()), role="helpdesk", clearance=None, department=None, employee_ref=None, helpdesk_ref="HD-901")
_HELPDESK_OTHER = Principal(kind="user", user_id=str(uuid.uuid4()), role="helpdesk", clearance=None, department=None, employee_ref=None, helpdesk_ref="HD-902")
_ADMIN = Principal(kind="user", user_id=str(uuid.uuid4()), role="admin", clearance=None, department=None, employee_ref=None, helpdesk_ref=None)
_GUEST = Principal(kind="guest", user_id=None, role="guest", clearance=None, department=None, employee_ref=None, helpdesk_ref=None, guest_name="G", guest_email="guest@example.com")


def _scoped_ids(db_session, principal, guest_email=None):
    query = scope_tickets_query(db_session.query(Ticket), principal, guest_email=guest_email)
    return {t.id for t in query.all()}


def test_employee_sees_only_tickets_they_requested(db_session, make_ticket):
    mine = make_ticket(requester_user_id=_REQUESTER_ID, requester_guest_email=None)
    theirs = make_ticket(requester_user_id=_OTHER_ID, requester_guest_email=None)

    ids = _scoped_ids(db_session, _EMPLOYEE)

    assert mine.id in ids
    assert theirs.id not in ids


def test_helpdesk_sees_tickets_assigned_to_them_not_ones_they_requested(db_session, make_ticket):
    assigned = make_ticket(assignee_helpdesk_ref="HD-901")
    other = make_ticket(assignee_helpdesk_ref="HD-902")

    ids = _scoped_ids(db_session, _HELPDESK)

    assert assigned.id in ids
    assert other.id not in ids


def test_admin_is_unrestricted(db_session, make_ticket):
    a = make_ticket(assignee_helpdesk_ref="HD-901", requester_user_id=_REQUESTER_ID)
    b = make_ticket(assignee_helpdesk_ref="HD-902", requester_user_id=_OTHER_ID)

    ids = _scoped_ids(db_session, _ADMIN)

    assert {a.id, b.id} <= ids


def test_guest_sees_only_their_own_email(db_session, make_ticket):
    mine = make_ticket(requester_guest_email="guest@example.com")
    theirs = make_ticket(requester_guest_email="someone-else@example.com")

    ids = _scoped_ids(db_session, _GUEST, guest_email="guest@example.com")

    assert mine.id in ids
    assert theirs.id not in ids


def test_helpdesk_with_no_ref_sees_nothing_rather_than_everything(db_session, make_ticket):
    """Fail closed: a helpdesk principal whose JWT carries no helpdesk_ref
    must match zero rows, never fall through to an unfiltered query."""
    make_ticket(assignee_helpdesk_ref="HD-901")
    broken = Principal(kind="user", user_id=str(uuid.uuid4()), role="helpdesk", clearance=None, department=None, employee_ref=None, helpdesk_ref=None)

    assert _scoped_ids(db_session, broken) == set()


def test_can_read_ticket_agrees_with_the_query_filter(db_session, make_ticket):
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", requester_user_id=_REQUESTER_ID)

    assert can_read_ticket(_EMPLOYEE, ticket) is True
    assert can_read_ticket(_OTHER_EMPLOYEE, ticket) is False
    assert can_read_ticket(_HELPDESK, ticket) is True
    assert can_read_ticket(_HELPDESK_OTHER, ticket) is False
    assert can_read_ticket(_ADMIN, ticket) is True
    assert can_read_ticket(_GUEST, ticket, guest_email="guest@example.com") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_tickets_scoping.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.tickets.scoping'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/tickets/scoping.py`:

```python
from __future__ import annotations

import uuid

from app.db.models import Ticket
from app.rbac.policy import Principal


def scope_tickets_query(query, principal: Principal, *, guest_email: str | None = None):
    """Spec 6.4: filter on requester identity for employees, on assignee for
    helpdesk, unrestricted for admin. This is the ONLY place that rule is
    written -- both the agent tools and the HTTP endpoints route through it,
    so the two can never drift apart.

    Fails closed by construction: every branch either returns a filtered
    query or (admin only) the unfiltered one, and a principal missing the
    identifier its branch needs matches zero rows rather than falling
    through to an unfiltered query.
    """
    if principal.role == "admin":
        return query
    if principal.role == "helpdesk":
        # helpdesk_ref None -> `Ticket.assignee_helpdesk_ref == None` -> no rows.
        return query.filter(Ticket.assignee_helpdesk_ref == principal.helpdesk_ref)
    if principal.kind == "guest":
        email = guest_email if guest_email is not None else principal.guest_email
        return query.filter(Ticket.requester_guest_email == email)
    if principal.user_id is None:
        return query.filter(Ticket.id.is_(None))
    return query.filter(Ticket.requester_user_id == uuid.UUID(principal.user_id))


def can_read_ticket(principal: Principal, ticket: Ticket, *, guest_email: str | None = None) -> bool:
    """Single-row form of scope_tickets_query, for endpoints and tools that
    fetch a ticket by id. Kept deliberately parallel to the query version --
    a test asserts the two agree on the same rows."""
    if principal.role == "admin":
        return True
    if principal.role == "helpdesk":
        return principal.helpdesk_ref is not None and ticket.assignee_helpdesk_ref == principal.helpdesk_ref
    if principal.kind == "guest":
        email = guest_email if guest_email is not None else principal.guest_email
        return email is not None and ticket.requester_guest_email == email
    if principal.user_id is None:
        return False
    return ticket.requester_user_id == uuid.UUID(principal.user_id)
```

- [ ] **Step 4: Route the agent tools through the shared chokepoint**

In `backend/app/agent/tools/tickets.py`, add the import:

```python
from app.tickets.scoping import can_read_ticket, scope_tickets_query
```

Delete the `_row_scope_filter` function entirely and replace the two handlers' scoping:

```python
async def list_my_tickets_handler(
    principal: Principal, db: Session, args: ListMyTicketsArgs, *, guest_email: str | None = None,
) -> dict:
    query = scope_tickets_query(db.query(Ticket), principal, guest_email=guest_email)
    if args.status is not None:
        query = query.filter(Ticket.status == TicketStatus(args.status))
    tickets = query.order_by(Ticket.created_at.desc()).all()
    return {
        "tickets": [
            {"ticket_number": f"TCK-{t.ticket_number:06d}", "title": t.title, "status": t.status.value, "priority": t.priority.value}
            for t in tickets
        ]
    }


async def get_ticket_handler(
    principal: Principal, db: Session, args: GetTicketArgs, *, guest_email: str | None = None,
) -> dict:
    ticket = db.get(Ticket, uuid.UUID(args.ticket_id))
    if ticket is None:
        return {"is_error": True, "content": "no such ticket"}
    if not can_read_ticket(principal, ticket, guest_email=guest_email):
        return {"is_error": True, "content": "you do not have access to this ticket"}
    return {
        "ticket_number": f"TCK-{ticket.ticket_number:06d}", "title": ticket.title, "body": ticket.body,
        "status": ticket.status.value, "priority": ticket.priority.value,
        "assignee_helpdesk_ref": ticket.assignee_helpdesk_ref,
    }
```

- [ ] **Step 5: Add the tool-level regression test the old code could not pass**

Add to `backend/tests/test_agent_tools_tickets.py`:

```python
async def test_list_my_tickets_returns_tickets_assigned_to_a_helpdesk_principal(db_session, make_ticket):
    """Spec 6.4 scopes list_my_tickets by ASSIGNEE for helpdesk. Before the
    shared scoping chokepoint this filtered on requester identity for every
    role, so a helpdesk user saw an empty list for tickets assigned to them."""
    import uuid as _uuid

    from app.agent.tools.tickets import ListMyTicketsArgs, list_my_tickets_handler
    from app.rbac.policy import Principal

    assigned = make_ticket(assignee_helpdesk_ref="HD-901", requester_user_id=None, requester_guest_email="someone@example.com")
    helpdesk = Principal(kind="user", user_id=str(_uuid.uuid4()), role="helpdesk", clearance=None, department=None, employee_ref=None, helpdesk_ref="HD-901")

    result = await list_my_tickets_handler(helpdesk, db_session, ListMyTicketsArgs())

    numbers = {t["ticket_number"] for t in result["tickets"]}
    assert f"TCK-{assigned.ticket_number:06d}" in numbers
```

- [ ] **Step 6: Run the scoping and tool tests**

Run: `cd backend && uv run pytest tests/test_tickets_scoping.py tests/test_agent_tools_tickets.py tests/test_rbac_authorize.py -v`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add backend/app/tickets/scoping.py backend/app/agent/tools/tickets.py backend/tests/test_tickets_scoping.py backend/tests/test_agent_tools_tickets.py
git commit -m "Add shared spec-6.4 ticket row-scoping chokepoint and route agent tools through it"
```

---

### Task 7: `GET /api/tickets` and `GET /api/tickets/{id}`

The read half of spec §14's ticket surface, plus the router registration every later endpoint needs.

**Files:**
- Create: `backend/app/tickets/router.py`
- Modify: `backend/app/main.py` (register the router)
- Test: `backend/tests/test_tickets_router.py` (new)

**Interfaces:**
- Consumes: `app.deps.{CurrentPrincipal, DbSession}`, `app.tickets.scoping.{scope_tickets_query, can_read_ticket}` (Task 6).
- Produces:
  - `app.tickets.router.router` (`APIRouter`, prefix `/api/tickets`)
  - `app.tickets.router.TicketSummary` / `TicketDetail` Pydantic response models
  - `app.tickets.router.serialize_detail(ticket) -> TicketDetail` — reused by Tasks 8 and 9

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_tickets_router.py`:

```python
from __future__ import annotations

import uuid

from app.db.models import EscalationAuthority, Role, TicketStatus, User


def _login(client, db_session, *, username: str, role: Role, helpdesk_ref: str | None = None) -> dict:
    from app.auth.security import hash_password

    user = User(
        username=username, email=f"{username}@northstar.example", full_name=username.title(),
        password_hash=hash_password("Passw0rd!dev"), role=role, helpdesk_ref=helpdesk_ref,
        specialization="Network and VPN Support" if helpdesk_ref else None,
        escalation_authority=EscalationAuthority.STANDARD if helpdesk_ref else None,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": username, "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return user, {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_list_tickets_requires_authentication(client):
    assert client.get("/api/tickets").status_code == 401


def test_employee_lists_only_their_own_tickets(client, db_session, make_ticket):
    user, headers = _login(client, db_session, username="ticketemp", role=Role.EMPLOYEE)
    mine = make_ticket(requester_user_id=user.id, title="Mine")
    make_ticket(requester_user_id=uuid.uuid4(), title="Theirs")

    resp = client.get("/api/tickets", headers=headers)

    assert resp.status_code == 200
    titles = {t["title"] for t in resp.json()}
    assert titles == {"Mine"}
    assert resp.json()[0]["ticket_number"] == f"TCK-{mine.ticket_number:06d}"


def test_helpdesk_lists_tickets_assigned_to_them(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="tickethd", role=Role.HELPDESK, helpdesk_ref="HD-901")
    make_ticket(assignee_helpdesk_ref="HD-901", title="Assigned to me")
    make_ticket(assignee_helpdesk_ref="HD-902", title="Someone else's")

    resp = client.get("/api/tickets", headers=headers)

    assert {t["title"] for t in resp.json()} == {"Assigned to me"}


def test_list_tickets_filters_by_status(client, db_session, make_ticket):
    user, headers = _login(client, db_session, username="ticketfilter", role=Role.EMPLOYEE)
    make_ticket(requester_user_id=user.id, status=TicketStatus.OPEN, title="Open one")
    make_ticket(requester_user_id=user.id, status=TicketStatus.IN_PROGRESS, title="In progress one")

    resp = client.get("/api/tickets?status=in_progress", headers=headers)

    assert {t["title"] for t in resp.json()} == {"In progress one"}


def test_list_tickets_rejects_an_unknown_status(client, db_session):
    _user, headers = _login(client, db_session, username="ticketbadstatus", role=Role.EMPLOYEE)
    assert client.get("/api/tickets?status=nonsense", headers=headers).status_code == 422


def test_get_ticket_returns_the_full_detail(client, db_session, make_ticket):
    user, headers = _login(client, db_session, username="ticketdetail", role=Role.EMPLOYEE)
    ticket = make_ticket(requester_user_id=user.id, title="Detail me")

    resp = client.get(f"/api/tickets/{ticket.id}", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Detail me"
    assert body["body"] == "Body"
    assert body["assignee_helpdesk_ref"] == "HD-901"
    assert body["assignment_rationale"] == "seeded by make_ticket"
    assert body["resolution"] is None


def test_get_ticket_a_principal_does_not_own_is_404_not_403(client, db_session, make_ticket):
    """Spec 6.4: 'there is no code path that returns a ticket the principal
    does not own'. 404 rather than 403 so the endpoint does not confirm the
    existence of tickets the caller may not see."""
    _user, headers = _login(client, db_session, username="ticketnosy", role=Role.EMPLOYEE)
    someone_elses = make_ticket(requester_user_id=uuid.uuid4())

    assert client.get(f"/api/tickets/{someone_elses.id}", headers=headers).status_code == 404


def test_get_nonexistent_ticket_is_404(client, db_session):
    _user, headers = _login(client, db_session, username="ticket404", role=Role.EMPLOYEE)
    assert client.get(f"/api/tickets/{uuid.uuid4()}", headers=headers).status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_tickets_router.py -v`
Expected: FAIL — every test returns 404 because no `/api/tickets` route is registered.

- [ ] **Step 3: Write the router**

Create `backend/app/tickets/router.py`:

```python
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.db.models import Ticket, TicketStatus
from app.deps import CurrentPrincipal, DbSession
from app.tickets.scoping import can_read_ticket, scope_tickets_query

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


class TicketSummary(BaseModel):
    id: str
    ticket_number: str
    title: str
    status: str
    priority: str
    assignee_helpdesk_ref: str
    created_at: str


class TicketDetail(TicketSummary):
    body: str
    matched_specialization: str
    assignment_rationale: str
    assignment_score: float
    resolution: str | None
    resolved_at: str | None


def _number(ticket: Ticket) -> str:
    return f"TCK-{ticket.ticket_number:06d}"


def serialize_summary(ticket: Ticket) -> TicketSummary:
    return TicketSummary(
        id=str(ticket.id), ticket_number=_number(ticket), title=ticket.title,
        status=ticket.status.value, priority=ticket.priority.value,
        assignee_helpdesk_ref=ticket.assignee_helpdesk_ref,
        created_at=ticket.created_at.isoformat(),
    )


def serialize_detail(ticket: Ticket) -> TicketDetail:
    return TicketDetail(
        **serialize_summary(ticket).model_dump(),
        body=ticket.body,
        matched_specialization=ticket.matched_specialization,
        assignment_rationale=ticket.assignment_rationale,
        assignment_score=float(ticket.assignment_score),
        resolution=ticket.resolution,
        resolved_at=ticket.resolved_at.isoformat() if ticket.resolved_at else None,
    )


def load_readable_ticket(db, principal, ticket_id: uuid.UUID) -> Ticket:
    """Spec 6.4 lookup used by every by-id endpoint in this module. Returns
    404 for both 'does not exist' and 'exists but is not yours' -- a 403
    would confirm the existence of tickets the caller may not see."""
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or not can_read_ticket(principal, ticket):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such ticket")
    return ticket


@router.get("", response_model=list[TicketSummary])
def list_tickets(
    principal: CurrentPrincipal,
    db: DbSession,
    ticket_status: TicketStatus | None = Query(default=None, alias="status"),
) -> list[TicketSummary]:
    query = scope_tickets_query(db.query(Ticket), principal)
    if ticket_status is not None:
        query = query.filter(Ticket.status == ticket_status)
    return [serialize_summary(t) for t in query.order_by(Ticket.created_at.desc()).all()]


@router.get("/{ticket_id}", response_model=TicketDetail)
def get_ticket(ticket_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> TicketDetail:
    return serialize_detail(load_readable_ticket(db, principal, ticket_id))
```

- [ ] **Step 4: Register the router**

In `backend/app/main.py`, add the import next to the existing router imports:

```python
from app.tickets.router import router as tickets_router
```

and register it after `chat_router`:

```python
app.include_router(tickets_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_tickets_router.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/tickets/router.py backend/app/main.py backend/tests/test_tickets_router.py
git commit -m "Add row-scoped GET /api/tickets and GET /api/tickets/{id}"
```

---

### Task 8: `PATCH /api/tickets/{id}` — helpdesk/admin mutations with audit

Spec §14 marks this endpoint "(helpdesk/admin)". Mutations are status change, priority change, and reassignment. Every one writes an `audit_log` row in the same transaction as the change.

**Files:**
- Modify: `backend/app/tickets/router.py`
- Test: `backend/tests/test_tickets_router_patch.py` (new)

**Interfaces:**
- Consumes: `app.tickets.service.{InvalidTransition, reassign, transition_status}` (Task 5), `app.audit.service.{record_audit, actor_from_principal}` (Task 2), `app.deps.require_role`.
- Produces: `PATCH /api/tickets/{ticket_id}` returning `TicketDetail`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_tickets_router_patch.py`:

```python
from __future__ import annotations

import uuid

from app.db.models import ActorType, AuditLog, EscalationAuthority, Role, TicketStatus, User


def _login(client, db_session, *, username: str, role: Role, helpdesk_ref: str | None = None):
    from app.auth.security import hash_password

    user = User(
        username=username, email=f"{username}@northstar.example", full_name=username.title(),
        password_hash=hash_password("Passw0rd!dev"), role=role, helpdesk_ref=helpdesk_ref,
        specialization="Network and VPN Support" if helpdesk_ref else None,
        escalation_authority=EscalationAuthority.STANDARD if helpdesk_ref else None,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": username, "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return user, {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_employee_cannot_patch_a_ticket_even_their_own(client, db_session, make_ticket):
    user, headers = _login(client, db_session, username="patchemp", role=Role.EMPLOYEE)
    ticket = make_ticket(requester_user_id=user.id)

    resp = client.patch(f"/api/tickets/{ticket.id}", json={"status": "in_progress"}, headers=headers)

    assert resp.status_code == 403


def test_helpdesk_advances_status_on_their_own_ticket(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="patchhd", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.OPEN)

    resp = client.patch(f"/api/tickets/{ticket.id}", json={"status": "in_progress"}, headers=headers)

    assert resp.status_code == 200
    assert resp.json()["status"] == "in_progress"
    db_session.refresh(ticket)
    assert ticket.status == TicketStatus.IN_PROGRESS


def test_helpdesk_cannot_patch_a_ticket_assigned_to_someone_else(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="patchhdother", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-902", status=TicketStatus.OPEN)

    assert client.patch(f"/api/tickets/{ticket.id}", json={"status": "in_progress"}, headers=headers).status_code == 404


def test_admin_can_patch_any_ticket(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="patchadmin", role=Role.ADMIN)
    ticket = make_ticket(assignee_helpdesk_ref="HD-902", status=TicketStatus.OPEN)

    assert client.patch(f"/api/tickets/{ticket.id}", json={"priority": "urgent"}, headers=headers).status_code == 200
    db_session.refresh(ticket)
    assert ticket.priority.value == "urgent"


def test_illegal_status_transition_is_409(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="patchillegal", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.CLOSED)

    resp = client.patch(f"/api/tickets/{ticket.id}", json={"status": "open"}, headers=headers)

    assert resp.status_code == 409
    assert "closed" in resp.json()["detail"]


def test_patch_with_no_fields_is_400(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="patchempty", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901")

    assert client.patch(f"/api/tickets/{ticket.id}", json={}, headers=headers).status_code == 400


def test_reassignment_updates_ref_and_rationale(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="patchreassign", role=Role.ADMIN)
    db_session.add(User(
        username="hd-980", email="hd-980@northstar.example", full_name="HD-980", password_hash="x",
        role=Role.HELPDESK, helpdesk_ref="HD-980", specialization="Identity and Access Management",
        escalation_authority=EscalationAuthority.HIGH,
    ))
    db_session.commit()
    ticket = make_ticket(assignee_helpdesk_ref="HD-901")

    resp = client.patch(
        f"/api/tickets/{ticket.id}",
        json={"assignee_helpdesk_ref": "HD-980", "reassignment_rationale": "Needs high escalation authority."},
        headers=headers,
    )

    assert resp.status_code == 200
    assert resp.json()["assignee_helpdesk_ref"] == "HD-980"
    assert "Needs high escalation authority." in resp.json()["assignment_rationale"]


def test_reassignment_without_a_rationale_is_400(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="patchnorationale", role=Role.ADMIN)
    ticket = make_ticket(assignee_helpdesk_ref="HD-901")

    assert client.patch(
        f"/api/tickets/{ticket.id}", json={"assignee_helpdesk_ref": "HD-980"}, headers=headers,
    ).status_code == 400


def test_every_successful_patch_writes_one_audit_row(client, db_session, make_ticket):
    """Spec 14: 'a role check plus an audit-log write on every mutating call'."""
    user, headers = _login(client, db_session, username="patchaudit", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.OPEN)

    client.patch(f"/api/tickets/{ticket.id}", json={"status": "in_progress"}, headers=headers)

    row = db_session.query(AuditLog).filter(
        AuditLog.action == "ticket.update", AuditLog.target_id == str(ticket.id),
    ).one()
    assert row.actor_type == ActorType.USER
    assert row.actor_id == str(user.id)
    assert row.target_type == "ticket"
    assert row.payload["changes"]["status"] == {"from": "open", "to": "in_progress"}


def test_a_rejected_patch_writes_no_audit_row(client, db_session, make_ticket):
    """record_audit deliberately does not commit, so a failed mutation must
    leave no audit trace behind."""
    _user, headers = _login(client, db_session, username="patchnoaudit", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.CLOSED)

    client.patch(f"/api/tickets/{ticket.id}", json={"status": "open"}, headers=headers)

    assert db_session.query(AuditLog).filter(AuditLog.target_id == str(ticket.id)).count() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_tickets_router_patch.py -v`
Expected: FAIL with 405 Method Not Allowed — the route does not exist.

- [ ] **Step 3: Write the endpoint**

In `backend/app/tickets/router.py`, extend the imports:

```python
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.audit.service import actor_from_principal, record_audit
from app.db.models import Ticket, TicketPriority, TicketStatus
from app.deps import CurrentPrincipal, DbSession, require_role
from app.rbac.policy import Principal
from app.tickets.scoping import can_read_ticket, scope_tickets_query
from app.tickets.service import InvalidTransition, reassign, transition_status
```

Then append to the file:

```python
# Spec 14 marks PATCH and resolve as helpdesk/admin. The role gate is the
# coarse check; load_readable_ticket then applies spec 6.4's row scoping, so
# a helpdesk user still only reaches tickets assigned to them.
StaffPrincipal = Annotated[Principal, Depends(require_role("helpdesk", "admin"))]


class UpdateTicketRequest(BaseModel):
    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    assignee_helpdesk_ref: str | None = None
    reassignment_rationale: str | None = None


@router.patch("/{ticket_id}", response_model=TicketDetail)
def update_ticket(
    ticket_id: uuid.UUID, payload: UpdateTicketRequest, principal: StaffPrincipal, db: DbSession,
) -> TicketDetail:
    ticket = load_readable_ticket(db, principal, ticket_id)

    if payload.status is None and payload.priority is None and payload.assignee_helpdesk_ref is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no fields to update")
    if payload.assignee_helpdesk_ref is not None and not (payload.reassignment_rationale or "").strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "reassignment_rationale is required when changing the assignee",
        )

    changes: dict[str, dict[str, str | None]] = {}

    if payload.priority is not None and payload.priority != ticket.priority:
        changes["priority"] = {"from": ticket.priority.value, "to": payload.priority.value}
        ticket.priority = payload.priority

    if payload.assignee_helpdesk_ref is not None and payload.assignee_helpdesk_ref != ticket.assignee_helpdesk_ref:
        changes["assignee_helpdesk_ref"] = {"from": ticket.assignee_helpdesk_ref, "to": payload.assignee_helpdesk_ref}
        reassign(
            db, ticket, assignee_helpdesk_ref=payload.assignee_helpdesk_ref,
            rationale=payload.reassignment_rationale.strip(),
        )

    if payload.status is not None and payload.status != ticket.status:
        previous = ticket.status.value
        try:
            transition_status(db, ticket, payload.status)
        except InvalidTransition as exc:
            # 409: the request is well-formed and authorized, but conflicts
            # with the ticket's current state.
            db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        changes["status"] = {"from": previous, "to": payload.status.value}

    actor_type, actor_id = actor_from_principal(principal)
    record_audit(
        db, actor_type=actor_type, actor_id=actor_id, action="ticket.update",
        target_type="ticket", target_id=str(ticket.id), payload={"changes": changes},
    )
    # One commit for the mutation and its audit row together -- spec 5.4's
    # append-only log must never describe a change that was rolled back.
    db.commit()
    db.refresh(ticket)
    return serialize_detail(ticket)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_tickets_router_patch.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/tickets/router.py backend/tests/test_tickets_router_patch.py
git commit -m "Add PATCH /api/tickets/{id} with role gate, transition validation, and audit"
```

---

### Task 9: `POST /api/tickets/{id}/resolve`

The last of spec §14's four ticket endpoints. Separate from PATCH because it carries required resolution text and stamps three columns, and because Phase 9's learning loop will hook exactly here.

**Files:**
- Modify: `backend/app/tickets/router.py`
- Test: `backend/tests/test_tickets_router_resolve.py` (new)

**Interfaces:**
- Consumes: `app.tickets.service.resolve_ticket` (Task 5), `app.audit.service` (Task 2).
- Produces: `POST /api/tickets/{ticket_id}/resolve` returning `TicketDetail`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_tickets_router_resolve.py`:

```python
from __future__ import annotations

from app.db.models import AuditLog, EscalationAuthority, Role, TicketStatus, User


def _login(client, db_session, *, username: str, role: Role, helpdesk_ref: str | None = None):
    from app.auth.security import hash_password

    user = User(
        username=username, email=f"{username}@northstar.example", full_name=username.title(),
        password_hash=hash_password("Passw0rd!dev"), role=role, helpdesk_ref=helpdesk_ref,
        specialization="Network and VPN Support" if helpdesk_ref else None,
        escalation_authority=EscalationAuthority.STANDARD if helpdesk_ref else None,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": username, "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return user, {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_helpdesk_resolves_their_own_ticket(client, db_session, make_ticket):
    user, headers = _login(client, db_session, username="resolvehd", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.IN_PROGRESS)

    resp = client.post(
        f"/api/tickets/{ticket.id}/resolve",
        json={"resolution": "Reissued the VPN certificate."}, headers=headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["resolution"] == "Reissued the VPN certificate."
    assert body["resolved_at"] is not None

    db_session.refresh(ticket)
    assert ticket.resolved_by_user_id == user.id


def test_employee_cannot_resolve(client, db_session, make_ticket):
    user, headers = _login(client, db_session, username="resolveemp", role=Role.EMPLOYEE)
    ticket = make_ticket(requester_user_id=user.id, status=TicketStatus.IN_PROGRESS)

    assert client.post(
        f"/api/tickets/{ticket.id}/resolve", json={"resolution": "I fixed it myself"}, headers=headers,
    ).status_code == 403


def test_helpdesk_cannot_resolve_someone_elses_ticket(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="resolvehdother", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-902", status=TicketStatus.IN_PROGRESS)

    assert client.post(
        f"/api/tickets/{ticket.id}/resolve", json={"resolution": "not mine"}, headers=headers,
    ).status_code == 404


def test_resolving_a_closed_ticket_is_409(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="resolveclosed", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.CLOSED)

    assert client.post(
        f"/api/tickets/{ticket.id}/resolve", json={"resolution": "too late"}, headers=headers,
    ).status_code == 409


def test_blank_resolution_is_422(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="resolveblank", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.IN_PROGRESS)

    assert client.post(
        f"/api/tickets/{ticket.id}/resolve", json={"resolution": "   "}, headers=headers,
    ).status_code == 422


def test_resolve_writes_an_audit_row(client, db_session, make_ticket):
    user, headers = _login(client, db_session, username="resolveaudit", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.IN_PROGRESS)

    client.post(f"/api/tickets/{ticket.id}/resolve", json={"resolution": "Done."}, headers=headers)

    row = db_session.query(AuditLog).filter(
        AuditLog.action == "ticket.resolve", AuditLog.target_id == str(ticket.id),
    ).one()
    assert row.actor_id == str(user.id)
    assert row.payload["resolution"] == "Done."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_tickets_router_resolve.py -v`
Expected: FAIL with 404 — the route does not exist.

- [ ] **Step 3: Write the endpoint**

In `backend/app/tickets/router.py`, extend the service import:

```python
from app.tickets.service import InvalidTransition, reassign, resolve_ticket, transition_status
```

and add `Field` to the Pydantic import:

```python
from pydantic import BaseModel, Field
```

Then append to the file:

```python
class ResolveTicketRequest(BaseModel):
    # min_length=1 catches "", and the service layer rejects whitespace-only
    # text; both paths matter, since a resolution is what Phase 9's learning
    # loop later reads.
    resolution: str = Field(min_length=1)


@router.post("/{ticket_id}/resolve", response_model=TicketDetail)
def resolve_ticket_endpoint(
    ticket_id: uuid.UUID, payload: ResolveTicketRequest, principal: StaffPrincipal, db: DbSession,
) -> TicketDetail:
    ticket = load_readable_ticket(db, principal, ticket_id)

    try:
        resolve_ticket(
            db, ticket, resolution=payload.resolution,
            resolved_by_user_id=uuid.UUID(principal.user_id) if principal.user_id else None,
        )
    except InvalidTransition as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except ValueError as exc:
        # Whitespace-only resolution: semantically a validation failure, so
        # 422 to match what Pydantic returns for the empty-string case.
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    actor_type, actor_id = actor_from_principal(principal)
    record_audit(
        db, actor_type=actor_type, actor_id=actor_id, action="ticket.resolve",
        target_type="ticket", target_id=str(ticket.id),
        payload={"resolution": ticket.resolution},
    )
    db.commit()
    db.refresh(ticket)
    return serialize_detail(ticket)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_tickets_router_resolve.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run every ticket test together, to catch cross-endpoint interference**

Run: `cd backend && uv run pytest tests/test_tickets_router.py tests/test_tickets_router_patch.py tests/test_tickets_router_resolve.py tests/test_tickets_lifecycle.py tests/test_tickets_scoping.py tests/test_tickets_service.py tests/test_tickets_routing.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add backend/app/tickets/router.py backend/tests/test_tickets_router_resolve.py
git commit -m "Add POST /api/tickets/{id}/resolve with resolution capture and audit"
```

---

### Task 10: Phase gate, deterministic half

Spec §18's phase-5 gate: "A conversation yields a `tasks` row and a ticket assigned to a specialist whose specialization matches the category."

This task proves it in two independently meaningful pieces, both free to run:

1. **Plumbing** — a scripted conversation through the real SSE endpoint produces a `tasks` row and a `tickets` row, with `resolution_path == ticketed` and `assignee_user_id` resolved.
2. **Routing quality** — `rank_specialists()` against the **real seeded `helpdesk` Chroma collection** returns, for each of several category/summary pairs, a top candidate whose `specialization` matches the category.

Piece 2 is where "whose specialization matches the category" is actually tested. It cannot be folded into piece 1: a scripted client supplies the `assignee_helpdesk_ref` itself, so asserting on it there would be tautological.

**Files:**
- Test: `backend/tests/test_phase5_gate.py` (new)

**Interfaces:**
- Consumes: everything built in Tasks 1–9, plus `tests/support/fake_anthropic`.
- Produces: nothing — pure verification.

**Prerequisite:** Chroma must be reachable and the `helpdesk` collection ingested. Verify before starting:

```bash
curl http://localhost:8000/api/v2/heartbeat
```

If that fails, the container is not port-published in this environment — fix that first rather than weakening the test.

- [ ] **Step 1: Write the routing-quality half**

Create `backend/tests/test_phase5_gate.py`:

```python
from __future__ import annotations

import uuid

import pytest

from app.db.models import ResolutionPath, RunStatus, RunTrigger, Task, Ticket
from app.tickets.routing import rank_specialists

# (category, problem summary, substrings any one of which the winning
# specialization may contain). Drawn from the dataset's own 25 helpdesk
# specializations -- these are deliberately distinct and single-holder
# (spec 8.4), which is what makes an exact top-1 expectation reasonable.
_ROUTING_CASES = [
    ("vpn_network", "I cannot connect to the corporate VPN from home; the client times out.", ("vpn", "network")),
    ("authentication_mfa", "My MFA token stopped working after I replaced my phone.", ("mfa", "authentication", "identity", "access")),
    ("database_access", "I need read access to the analytics reporting database.", ("database", "data")),
]


@pytest.fixture(autouse=True)
def _traced_run(cleanup_run):
    """rank_specialists queries Chroma through McpChromaBackend, which wraps
    every MCP call in a tracing span; span() hard-requires an active run."""
    from app.tracing import end_run, start_run

    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        yield
        end_run(handle, status=RunStatus.OK)
    except Exception:
        end_run(handle, status=RunStatus.ABORTED)
        raise
    finally:
        cleanup_run(handle.run_id)


@pytest.mark.parametrize("category,summary,expected_substrings", _ROUTING_CASES)
async def test_routing_picks_a_specialist_whose_specialization_matches_the_category(
    db_session, category, summary, expected_substrings,
):
    """The 'assigned to a specialist whose specialization matches the
    category' half of the spec-18 phase-5 gate, measured against the real
    seeded helpdesk collection -- not against fabricated candidates."""
    candidates = await rank_specialists(db_session, problem_summary=summary, category=category, severity="medium")

    assert candidates, f"no candidate returned for {category!r}"
    top = candidates[0]
    specialization = (top["specialization"] or "").lower()
    assert any(s in specialization for s in expected_substrings), (
        f"top candidate for {category!r} was {top['helpdesk_ref']} "
        f"({top['specialization']!r}), which matches none of {expected_substrings}"
    )
```

- [ ] **Step 2: Run the routing half**

Run: `cd backend && uv run pytest tests/test_phase5_gate.py -v`
Expected: PASS (3 parametrized cases). If a case fails, do **not** loosen the assertion to make it green — report the measured result. A genuine routing miss is exactly what this gate exists to surface.

- [ ] **Step 3: Write the plumbing half**

Append to `backend/tests/test_phase5_gate.py`:

```python
_GATE_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000005a1")
_gate_orphans: dict[str, list] = {"user_ids": [], "conversation_ids": []}


@pytest.fixture(scope="module", autouse=True)
def _sweep_gate_orphans_after_module():
    """The end-to-end test below must hard-commit its User/Conversation
    (run_turn's tracing session commits on its own connection and cannot see
    db_session's savepoint -- the cross-connection FK-visibility gap that bit
    every Phase 4 task from Task 6 onward). Those rows therefore survive
    db_session's rollback and must be swept here, after every test in this
    module has released its locks. Mirrors
    tests/test_chat_router.py::_cleanup_sse_test_orphans_after_module,
    including its UsageCounter sweep -- without which repeated suite runs
    accumulate rows against the same fixed user_key and eventually trip the
    real 30/hour cap, and the leaked User row breaks test_seed.py's exact
    `assert total == 126`."""
    yield

    from app.db.models import Conversation, Run, Span, Ticket, UsageCounter, User
    from app.db.session import get_sessionmaker

    Session = get_sessionmaker()
    with Session() as session:
        conv_ids = list(_gate_orphans["conversation_ids"])
        user_ids = list(_gate_orphans["user_ids"])
        if user_ids:
            session.query(UsageCounter).filter(UsageCounter.user_key.in_([str(u) for u in user_ids])).delete(synchronize_session=False)
        if conv_ids:
            session.query(Ticket).filter(Ticket.conversation_id.in_(conv_ids)).delete(synchronize_session=False)
            session.query(Task).filter(Task.conversation_id.in_(conv_ids)).delete(synchronize_session=False)
            run_ids = [row[0] for row in session.query(Run.id).filter(Run.conversation_id.in_(conv_ids))]
            if run_ids:
                session.query(Span).filter(Span.run_id.in_(run_ids)).delete(synchronize_session=False)
                session.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
            session.query(Conversation).filter(Conversation.id.in_(conv_ids)).delete(synchronize_session=False)
        if user_ids:
            session.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        session.commit()


async def test_a_scripted_conversation_yields_a_task_row_and_a_ticket(db_session):
    """The 'a conversation yields a tasks row and a ticket' half of the
    spec-18 phase-5 gate. The assignee ref is scripted here (a fake client
    cannot react to find_helpdesk_specialist's output), so this asserts the
    PLUMBING -- task row, ticket row, resolution_path, assignee_user_id
    resolution. Routing quality is the separately-measured half above, and
    the live-API test in Task 11 proves the model genuinely does both."""
    from app.agent.loop import run_turn
    from app.db.models import Conversation, EscalationAuthority, Role, TaskCategory, User
    from app.db.session import get_sessionmaker
    from app.rbac.policy import Principal
    from tests.support.fake_anthropic import (
        FakeAnthropicClient, make_text_message, make_tool_use_message,
    )

    Session = get_sessionmaker()
    with Session() as setup:
        user = setup.get(User, _GATE_USER_ID)
        if user is None:
            user = User(
                id=_GATE_USER_ID, username="phase5gate", email="phase5gate@northstar.example",
                full_name="Phase Five Gate", password_hash="x", role=Role.EMPLOYEE,
                clearance=None, employee_ref="EMP-5A1",
            )
            setup.add(user)
        specialist = setup.query(User).filter(User.helpdesk_ref == "HD-5A1").one_or_none()
        if specialist is None:
            specialist = User(
                username="hd-5a1", email="hd-5a1@northstar.example", full_name="HD-5A1",
                password_hash="x", role=Role.HELPDESK, helpdesk_ref="HD-5A1",
                specialization="Network and VPN Support", escalation_authority=EscalationAuthority.STANDARD,
            )
            setup.add(specialist)
        conv = Conversation(user_id=_GATE_USER_ID, title="Gate conversation")
        setup.add(conv)
        setup.commit()
        conversation_id = conv.id
        specialist_id = specialist.id

    _gate_orphans["user_ids"].append(_GATE_USER_ID)
    _gate_orphans["conversation_ids"].append(conversation_id)

    principal = Principal(
        kind="user", user_id=str(_GATE_USER_ID), role="employee", clearance="standard",
        department="Engineering", employee_ref="EMP-5A1", helpdesk_ref=None,
    )

    # The scripted turn: classify -> file a ticket -> answer.
    client = FakeAnthropicClient([
        make_tool_use_message(tool_name="record_task", tool_use_id="tu1", tool_input={
            "conversation_id": str(conversation_id),
            "title": "VPN client times out",
            "category": TaskCategory.VPN_NETWORK.value,
            "severity": "medium",
            "summary": "User cannot connect to the corporate VPN from home.",
            "affected_systems": ["vpn"],
            "evidence": {"error": "timeout"},
        }),
        make_text_message(text="I have recorded the problem."),
    ])

    task_id: str | None = None
    async for event in run_turn(
        client, db_session, principal, conversation_id=conversation_id,
        user_key=str(_GATE_USER_ID), history=[], user_message="My VPN will not connect.",
    ):
        if event.type == "task_recorded":
            task_id = event.data["task_id"]

    assert task_id is not None, "the scripted record_task call produced no task_recorded event"

    task = db_session.get(Task, uuid.UUID(task_id))
    assert task is not None
    assert task.category == TaskCategory.VPN_NETWORK

    # Second turn: now that task_id exists, script the create_ticket call.
    client2 = FakeAnthropicClient([
        make_tool_use_message(tool_name="create_ticket", tool_use_id="tu2", tool_input={
            "task_id": task_id,
            "assignee_helpdesk_ref": "HD-5A1",
            "priority": "medium",
            "title": "VPN client times out",
            "body": "User cannot connect to the corporate VPN from home.",
            "assignment_rationale": "Semantic match rank 1; current workload: 0 open ticket(s).",
            "matched_specialization": "Network and VPN Support",
            "assignment_score": 0.95,
        }),
        make_text_message(text="Ticket filed."),
    ])

    async for event in run_turn(
        client2, db_session, principal, conversation_id=conversation_id,
        user_key=str(_GATE_USER_ID), history=[], user_message="Please raise a ticket.",
    ):
        pass

    ticket = db_session.query(Ticket).filter(Ticket.task_id == uuid.UUID(task_id)).one()
    assert ticket.assignee_helpdesk_ref == "HD-5A1"
    assert ticket.assignee_user_id == specialist_id, "create_ticket did not resolve the assignee FK"

    db_session.refresh(task)
    assert task.resolution_path == ResolutionPath.TICKETED, "task was not marked ticketed"
```

Note on the two-client structure: `FakeAnthropicClient` is scripted with a fixed list of responses and cannot react to a tool's output, so `create_ticket`'s `task_id` argument is only knowable *after* the first turn has run. Hence two clients and two `run_turn` calls rather than one longer script — this is a limitation of the fake, not a claim about how the real loop behaves (the live test in Task 11 does it in a single turn).

- [ ] **Step 4: Run the whole gate file**

Run: `cd backend && uv run pytest tests/test_phase5_gate.py -v`
Expected: PASS (4 tests: 3 routing + 1 plumbing)

- [ ] **Step 5: Verify the database is left clean**

Run:

```bash
cd backend && uv run python -c "
from app.db.session import get_sessionmaker
from app.db.models import User, Conversation, Task, Ticket
S = get_sessionmaker()
with S() as s:
    print('users', s.query(User).count())
    print('gate convs', s.query(Conversation).filter(Conversation.title == 'Gate conversation').count())
"
```

Expected: `users 126` (the seeded count, proving no leak) and `gate convs 0`.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/test_phase5_gate.py
git commit -m "Add phase-5 gate: scripted conversation yields task and ticket, routing matches category"
```

---

### Task 11: Live-API gate verification

**Run this task yourself. Do not delegate it to a subagent.** It spends real Anthropic tokens. Phase 4 established this rule and its live run caught a blocking bug (a Pydantic `le=` bound emitting a JSON-schema `maximum` keyword the real API rejects) that every prior review had waved through as low-risk.

The scripted gate in Task 10 proves the plumbing and the routing algorithm. It cannot prove the thing the gate sentence actually claims: that a **real conversation** with a **real model** produces a correctly-routed ticket. That is what this task measures.

**Files:**
- Test: `backend/tests/test_tickets_live_api.py` (new)

**Interfaces:**
- Consumes: everything. Produces: nothing.

- [ ] **Step 1: Confirm the marker is registered and excluded by default**

Run: `cd backend && grep -n "live_api" pyproject.toml`
Expected: the `live_api` marker is registered and the default `addopts` deselects it (Phase 4 set this up — spec §19: "Live-API tests are marked and excluded from the default run; the default `pytest` invocation costs nothing"). If it is missing, stop and fix that before writing the test.

- [ ] **Step 2: Write the live test**

Create `backend/tests/test_tickets_live_api.py`:

```python
from __future__ import annotations

import uuid

import pytest

from app.db.models import ResolutionPath, Task, Ticket

pytestmark = pytest.mark.live_api

_LIVE_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000005b1")


@pytest.mark.live_api
async def test_a_real_conversation_yields_a_task_and_a_matching_ticket(db_session):
    """THE spec-18 phase-5 gate, end to end against the real API: a real
    conversation must yield a tasks row AND a ticket whose assignee's
    specialization matches the category the model itself chose.

    Unlike the scripted half, nothing here is fed to the model -- it picks
    the category, calls find_helpdesk_specialist, and chooses the assignee
    on its own. Assertions are on measured outcomes, printed so the numbers
    go into the phase report rather than being merely 'green'.
    """
    import anthropic

    from app.agent.loop import run_turn
    from app.config import get_settings
    from app.db.models import Conversation, Role, User
    from app.db.session import get_sessionmaker
    from app.rbac.policy import Principal

    Session = get_sessionmaker()
    with Session() as setup:
        user = setup.get(User, _LIVE_USER_ID)
        if user is None:
            setup.add(User(
                id=_LIVE_USER_ID, username="phase5live", email="phase5live@northstar.example",
                full_name="Phase Five Live", password_hash="x", role=Role.EMPLOYEE,
                clearance="standard", department="Engineering", employee_ref="EMP-5B1",
            ))
        conv = Conversation(user_id=_LIVE_USER_ID, title="Live gate conversation")
        setup.add(conv)
        setup.commit()
        conversation_id = conv.id

    principal = Principal(
        kind="user", user_id=str(_LIVE_USER_ID), role="employee", clearance="standard",
        department="Engineering", employee_ref="EMP-5B1", helpdesk_ref=None,
    )
    client = anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key)

    message = (
        "I can't connect to the corporate VPN from my home office. The client "
        "sits at 'negotiating' for about 30 seconds and then times out. I've "
        "rebooted and reinstalled it. Please raise a ticket for this."
    )

    events = []
    async for event in run_turn(
        client, db_session, principal, conversation_id=conversation_id,
        user_key=str(_LIVE_USER_ID), history=[], user_message=message,
    ):
        events.append(event)

    kinds = [e.type for e in events]
    print(f"\nevent types: {kinds}")

    task = db_session.query(Task).filter(Task.conversation_id == conversation_id).one_or_none()
    assert task is not None, f"no tasks row was written; events were {kinds}"
    print(f"task: category={task.category.value} severity={task.severity.value} title={task.title!r}")

    ticket = db_session.query(Ticket).filter(Ticket.conversation_id == conversation_id).one_or_none()
    assert ticket is not None, f"no ticket was created; events were {kinds}"

    specialist = db_session.query(User).filter(User.helpdesk_ref == ticket.assignee_helpdesk_ref).one_or_none()
    print(
        f"ticket: TCK-{ticket.ticket_number:06d} -> {ticket.assignee_helpdesk_ref} "
        f"({specialist.specialization if specialist else 'UNKNOWN REF'}) "
        f"score={float(ticket.assignment_score)}"
    )
    print(f"rationale: {ticket.assignment_rationale}")

    assert specialist is not None, f"model assigned {ticket.assignee_helpdesk_ref!r}, which is not a real helpdesk ref"
    assert ticket.assignee_user_id == specialist.id

    specialization = (specialist.specialization or "").lower()
    assert any(s in specialization for s in ("vpn", "network")), (
        f"gate FAILED: category {task.category.value!r} routed to {specialist.specialization!r}"
    )

    db_session.refresh(task)
    assert task.resolution_path == ResolutionPath.TICKETED
```

- [ ] **Step 3: Confirm prerequisites, then run the live test — once**

`.env` must carry a working `ANTHROPIC_API_KEY`, and Chroma must be reachable:

```bash
curl http://localhost:8000/api/v2/heartbeat
```

Then run exactly this, once:

```bash
cd backend && uv run pytest tests/test_tickets_live_api.py -v -m live_api -s
```

`-s` matters: the printed task/ticket/specialization lines are the measured evidence for the phase report. Record them verbatim.

- [ ] **Step 4: Clean up the rows the live run hard-committed**

```bash
cd backend && uv run python -c "
from app.db.session import get_sessionmaker
from app.db.models import Conversation, Run, Span, Task, Ticket, UsageCounter, User
import uuid
LIVE = uuid.UUID('00000000-0000-0000-0000-0000000005b1')
S = get_sessionmaker()
with S() as s:
    ids = [c.id for c in s.query(Conversation).filter(Conversation.user_id == LIVE)]
    if ids:
        s.query(Ticket).filter(Ticket.conversation_id.in_(ids)).delete(synchronize_session=False)
        s.query(Task).filter(Task.conversation_id.in_(ids)).delete(synchronize_session=False)
        runs = [r[0] for r in s.query(Run.id).filter(Run.conversation_id.in_(ids))]
        if runs:
            s.query(Span).filter(Span.run_id.in_(runs)).delete(synchronize_session=False)
            s.query(Run).filter(Run.id.in_(runs)).delete(synchronize_session=False)
        s.query(Conversation).filter(Conversation.id.in_(ids)).delete(synchronize_session=False)
    s.query(UsageCounter).filter(UsageCounter.user_key == str(LIVE)).delete(synchronize_session=False)
    s.query(User).filter(User.id == LIVE).delete(synchronize_session=False)
    s.commit()
    print('users now', s.query(User).count())
"
```

Expected: `users now 126`.

- [ ] **Step 5: Run the complete default suite**

Run: `cd backend && uv run python tasks.py test`
Expected: all pass except possibly the two known flakes listed in Global Constraints. Any other failure is a regression and must be fixed before the phase closes.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/test_tickets_live_api.py
git commit -m "Add live-API phase-5 gate: real conversation yields a task and a category-matched ticket"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| §5.3 `tasks.resolution_path` → `ticketed` | 4 |
| §5.3 `tickets.assignee_user_id` populated | 4 |
| §5.3 `tickets` resolution columns (`resolution`, `resolved_by_user_id`, `resolved_at`) | 5 |
| §5.4 `audit_log` append-only, no update/delete path | 2 |
| §6.3 Deny → `audit_log` row (Phase 4 gap) | 3 |
| §6.4 row scoping: requester/assignee/admin, no leaking code path | 6, 7 |
| §8.4 specialist routing, three signals, rationale stored | 1 |
| §14 `GET /api/tickets` | 7 |
| §14 `GET /api/tickets/{id}` | 7 |
| §14 `PATCH /api/tickets/{id}` (helpdesk/admin) | 8 |
| §14 `POST /api/tickets/{id}/resolve` | 9 |
| §14 audit-log write on every mutating call | 8, 9 |
| §16 `tickets/ router.py service.py routing.py` layout | 1, 5, 7 |
| §18 phase-5 gate | 10, 11 |

No spec requirement in Phase 5's scope is unassigned.

**Deliberate non-coverage (restating the out-of-scope list):** §15 admin screens, §10 notifications, §13 learning-loop writes on resolution, §9 approval decide/execute, §11 multimodal. Each belongs to a later phase per §18's table.

**Type consistency check:** `rank_specialists` is defined in Task 1 and consumed in Tasks 1 and 10 with the same keyword-only signature. `scope_tickets_query`/`can_read_ticket` are defined in Task 6 and consumed in Tasks 6, 7. `transition_status`/`reassign`/`resolve_ticket` are defined in Task 5 and consumed in Tasks 8 and 9 — all three stage-without-committing, and every consumer commits explicitly. `record_audit`/`actor_from_principal` are defined in Task 2 and consumed in Tasks 3, 8, 9 with matching keyword arguments. `serialize_detail`/`load_readable_ticket`/`StaffPrincipal` are defined in Task 7 and reused in Tasks 8 and 9. `make_ticket` is added to `conftest.py` in Task 1 and used in Tasks 1, 5, 6, 7, 8, 9.

**Known deviation recorded deliberately:** `app/audit/` is not in spec §16's repository layout. Neither is `app/chat/`, which Phase 4 created and which was accepted; a dedicated module is what keeps the single append-only write path obvious.
