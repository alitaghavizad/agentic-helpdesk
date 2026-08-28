# Phase 6 — Approvals, Executor, Notifications, Email Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An admin can approve a pending `approval_request`; the system re-validates it, executes the action, records the result, emails where the action is an email, and pushes an in-app notification over SSE — with the "no email without approval" invariant enforced by the database itself.

**Architecture:** `approvals.service.decide()` owns the state machine and calls `approvals.executor.execute_traced()` synchronously inside the admin's HTTP request. The executor re-loads the requester from current state, re-validates the payload, re-runs policy, then dispatches through a registry keyed by `ApprovalActionType`. Notifications are rows plus an in-process broker that publishes on transaction commit; a per-user SSE endpoint subscribes then replays unread rows.

**Tech Stack:** FastAPI, SQLAlchemy 2.x, Alembic, Postgres 18, `smtplib` (stdlib), pytest, `httpx.ASGITransport` for SSE tests.

## Global Constraints

- Spec of record: `docs/superpowers/specs/2026-08-27-approvals-executor-notifications-design.md`. Parent: `docs/superpowers/specs/2026-08-24-agentic-helpdesk-design.md`.
- Run everything from `backend/`: `uv run python tasks.py test`. **`make` is not installed.** Single test: `uv run pytest tests/test_x.py::test_y -v`.
- Full suite baseline before this phase: **327 passed, 0 failed**. It must stay at 0 failed. Full suite takes ≈8 minutes.
- **There are no known-flaky tests in this project.** A failing test is real until measured otherwise.
- **Never** call `tracing.start_run()` in a test that then inserts an FK-referencing row through `db_session` — it deadlocks Postgres. Build `Run` rows through `db_session`, as `tests/conftest.py::make_ticket` does.
- `app/tickets/service.py`'s `transition_status`, `reassign`, and `resolve_ticket` **stage without committing**; `create_ticket` and `chat.service.append_message` **do commit**. Match whichever you are editing.
- `tracing.span`'s context-manager form is **async-only**. Use the `@span(kind, name)` decorator form on sync functions.
- `audit.record_audit` stages and flushes but never commits — it belongs to the caller's transaction.
- Every new module gets a docstring explaining *why*, matching the density of `app/approvals/service.py` and `app/tickets/service.py`.
- Commit after every task. Never use `--no-verify`.

---

### Task 1: Migration — the invariant constraint, the run trigger, the index

**Files:**
- Modify: `backend/app/db/models.py` (add `RunTrigger.APPROVAL_EXECUTION`; add `OutboundEmail.approval_status_at_send`)
- Create: `backend/alembic/versions/<generated>_phase6_approval_invariant.py`
- Create: `backend/tests/test_approvals_invariant.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RunTrigger.APPROVAL_EXECUTION`; `OutboundEmail.approval_status_at_send: Mapped[ApprovalStatus]`. Task 4 sets that column on every insert.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_approvals_invariant.py`:

```python
"""Spec 5.3's 'no outbound_emails row without an approval' invariant, proven
against the database rather than against application code. These tests
deliberately bypass app/notifications/email.py and issue raw SQL: the point
is that a violation is impossible even for someone at a psql prompt."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    ApprovalActionType, ApprovalRequest, ApprovalStatus, Conversation, RiskLevel,
)


@pytest.fixture()
def approval(db_session):
    """A pending approval attached to a real conversation. approval_requests
    .conversation_id is a NOT NULL FK, so the conversation must exist first."""
    conv = Conversation(guest_name="Guest", guest_email="guest@northstar.example")
    db_session.add(conv)
    db_session.flush()
    request = ApprovalRequest(
        conversation_id=conv.id, task_id=None, requester_user_id=None,
        action_type=ApprovalActionType.SEND_EMAIL,
        action_payload={"to": "guest@northstar.example", "subject": "s", "body": "b"},
        justification="j", risk_level=RiskLevel.LOW, agent_summary="a",
        status=ApprovalStatus.PENDING,
    )
    db_session.add(request)
    db_session.flush()
    return request


def _insert_email(db_session, approval_id: uuid.UUID, shadow: str) -> None:
    db_session.execute(
        text(
            "INSERT INTO outbound_emails "
            "(id, approval_request_id, approval_status_at_send, to_address, subject, body, status) "
            "VALUES (:id, :aid, CAST(:shadow AS approval_status), 'x@northstar.example', 's', 'b', 'queued')"
        ),
        {"id": uuid.uuid4(), "aid": approval_id, "shadow": shadow},
    )


def test_email_row_naming_a_pending_approval_is_rejected(db_session, approval):
    """The CHECK forbids the shadow value 'pending' outright."""
    with pytest.raises(IntegrityError):
        _insert_email(db_session, approval.id, "pending")


def test_email_row_lying_about_the_approval_status_is_rejected(db_session, approval):
    """Claiming 'approved' while the approval is really 'pending' violates the
    composite FK -- the shadow column cannot be forged independently."""
    with pytest.raises(IntegrityError):
        _insert_email(db_session, approval.id, "approved")


def test_email_row_is_accepted_once_the_approval_is_approved(db_session, approval):
    approval.status = ApprovalStatus.APPROVED
    db_session.flush()
    _insert_email(db_session, approval.id, "approved")
    count = db_session.execute(
        text("SELECT count(*) FROM outbound_emails WHERE approval_request_id = :aid"),
        {"aid": approval.id},
    ).scalar_one()
    assert count == 1


def test_shadow_column_follows_the_approval_to_executed(db_session, approval):
    """ON UPDATE CASCADE: the normal approved -> executed transition must not
    need the email row to be touched, and must not break the CHECK."""
    approval.status = ApprovalStatus.APPROVED
    db_session.flush()
    _insert_email(db_session, approval.id, "approved")

    db_session.execute(
        text("UPDATE approval_requests SET status = 'executed' WHERE id = :aid"),
        {"aid": approval.id},
    )
    shadow = db_session.execute(
        text("SELECT approval_status_at_send FROM outbound_emails WHERE approval_request_id = :aid"),
        {"aid": approval.id},
    ).scalar_one()
    assert shadow == "executed"


def test_approval_with_an_email_cannot_be_flipped_to_denied(db_session, approval):
    """The cascade would drive the shadow column to 'denied', which the CHECK
    rejects -- so the whole UPDATE fails. An email cannot be retroactively
    orphaned from its approval."""
    approval.status = ApprovalStatus.APPROVED
    db_session.flush()
    _insert_email(db_session, approval.id, "approved")

    with pytest.raises(IntegrityError):
        db_session.execute(
            text("UPDATE approval_requests SET status = 'denied' WHERE id = :aid"),
            {"aid": approval.id},
        )


def test_failed_is_permitted_so_a_failed_send_can_be_recorded(db_session, approval):
    """Spec amendment 2.3: a legitimately failed send leaves both rows behind,
    so excluding 'failed' would make the failure path un-executable."""
    approval.status = ApprovalStatus.APPROVED
    db_session.flush()
    _insert_email(db_session, approval.id, "approved")

    db_session.execute(
        text("UPDATE approval_requests SET status = 'failed' WHERE id = :aid"),
        {"aid": approval.id},
    )
    shadow = db_session.execute(
        text("SELECT approval_status_at_send FROM outbound_emails WHERE approval_request_id = :aid"),
        {"aid": approval.id},
    ).scalar_one()
    assert shadow == "failed"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_approvals_invariant.py -v`
Expected: FAIL — `column "approval_status_at_send" of relation "outbound_emails" does not exist`.

- [ ] **Step 3: Add the model changes**

In `backend/app/db/models.py`, add to `RunTrigger`:

```python
    APPROVAL_EXECUTION = "approval_execution"
```

Add to `OutboundEmail`, immediately after `approval_request_id`:

```python
    # Spec 5.3's invariant lives in the database, not in application code:
    # this column mirrors the approval's status through a composite FK with
    # ON UPDATE CASCADE, and a CHECK forbids the pre-approval states. Never
    # set it by hand to something the approval is not actually in -- the FK
    # will reject the row. See the phase 6 spec section 7.
    approval_status_at_send: Mapped[ApprovalStatus] = mapped_column(
        SAEnum(ApprovalStatus, name="approval_status", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
```

- [ ] **Step 4: Generate the migration**

Run: `uv run python -m alembic revision -m "phase6 approval invariant"`

Then replace the generated `upgrade()`/`downgrade()` bodies. `down_revision` must be `'0d9825e8642c'`. Do **not** use autogenerate — it cannot express a composite FK against a non-PK unique constraint.

```python
def upgrade() -> None:
    # New enum value must be committed before any DDL below can reference it.
    op.execute("ALTER TYPE run_trigger ADD VALUE IF NOT EXISTS 'approval_execution'")
    op.execute("COMMIT")

    # Sole purpose: be a valid target for the composite FK below. id is
    # already the PK, so this constraint is trivially satisfied and costs
    # only the index Postgres builds for it.
    op.create_unique_constraint(
        "uq_approval_requests_id_status", "approval_requests", ["id", "status"],
    )

    # outbound_emails is empty at this point in the project's life (nothing
    # has ever written to it), so the column can be added NOT NULL with no
    # backfill. Guard anyway so a re-run against a populated table fails
    # loudly rather than silently inventing a status.
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM outbound_emails")).scalar_one()
    if count:
        raise RuntimeError(
            f"outbound_emails already has {count} rows; this migration assumes an "
            "empty table and has no backfill strategy for approval_status_at_send"
        )

    op.add_column(
        "outbound_emails",
        sa.Column(
            "approval_status_at_send",
            postgresql.ENUM(name="approval_status", create_type=False),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_outbound_emails_approval_status",
        "outbound_emails",
        "approval_requests",
        ["approval_request_id", "approval_status_at_send"],
        ["id", "status"],
        onupdate="CASCADE",
    )
    op.create_check_constraint(
        "ck_outbound_emails_approved_before_send",
        "outbound_emails",
        "approval_status_at_send IN ('approved', 'executed', 'failed')",
    )

    op.create_index(
        "ix_notifications_user_id_read_at", "notifications", ["user_id", "read_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_user_id_read_at", table_name="notifications")
    op.drop_constraint("ck_outbound_emails_approved_before_send", "outbound_emails", type_="check")
    op.drop_constraint("fk_outbound_emails_approval_status", "outbound_emails", type_="foreignkey")
    op.drop_column("outbound_emails", "approval_status_at_send")
    op.drop_constraint("uq_approval_requests_id_status", "approval_requests", type_="unique")
    # An enum value cannot be removed from a Postgres type in place; leaving
    # 'approval_execution' behind is harmless and is the standard downgrade
    # compromise for ALTER TYPE ... ADD VALUE.
```

Ensure the imports at the top include `from alembic import op`, `import sqlalchemy as sa`, and `from sqlalchemy.dialects import postgresql`.

- [ ] **Step 5: Apply the migration**

Run: `uv run python -m alembic upgrade head`
Expected: completes with no error. If `ALTER TYPE ... ADD VALUE` errors with "cannot run inside a transaction block", confirm the `op.execute("COMMIT")` line is present and directly follows it.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_approvals_invariant.py -v`
Expected: 6 passed.

- [ ] **Step 7: Verify the schema test still reflects reality**

Run: `uv run pytest tests/test_db_schema.py -v`
Expected: PASS. If it enumerates columns or enum values, add `approval_status_at_send` and `approval_execution` there.

- [ ] **Step 8: Commit**

```bash
git add backend/app/db/models.py backend/alembic/versions backend/tests/test_approvals_invariant.py backend/tests/test_db_schema.py
git commit -m "Enforce the no-email-without-approval invariant in the database"
```

---

### Task 2: The notification broker

**Files:**
- Create: `backend/app/notifications/__init__.py` (empty)
- Create: `backend/app/notifications/broker.py`
- Create: `backend/tests/test_notifications_broker.py`

**Interfaces:**
- Consumes: nothing. This module has no database dependency and imports nothing from `app.db`.
- Produces:
  - `broker.publish(user_id: uuid.UUID, event: dict) -> None`
  - `broker.subscribe(user_id: uuid.UUID) -> Subscription` — a SYNC context manager yielding an object consumed with `await sub.get()`
  - `broker.SubscriberDropped` — raised by `get()` once a subscriber that fell behind has delivered its buffered events; the SSE endpoint MUST catch it and close the stream
  - `broker.subscriber_count(user_id: uuid.UUID) -> int`
  - Task 3 calls `publish`; Task 8 calls `subscribe`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_notifications_broker.py`:

```python
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.notifications import broker


@pytest.mark.asyncio
async def test_publish_reaches_every_subscriber_for_that_user():
    user = uuid.uuid4()
    with broker.subscribe(user) as a, broker.subscribe(user) as b:
        broker.publish(user, {"type": "approval_decided", "id": "1"})
        assert await asyncio.wait_for(a.get(), timeout=1) == {"type": "approval_decided", "id": "1"}
        assert await asyncio.wait_for(b.get(), timeout=1) == {"type": "approval_decided", "id": "1"}


@pytest.mark.asyncio
async def test_publish_does_not_leak_across_users():
    alice, bob = uuid.uuid4(), uuid.uuid4()
    with broker.subscribe(alice) as a, broker.subscribe(bob) as b:
        broker.publish(alice, {"type": "ticket_created"})
        assert await asyncio.wait_for(a.get(), timeout=1) == {"type": "ticket_created"}
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(b.get(), timeout=0.05)


def test_publish_with_no_subscribers_is_a_no_op():
    """The row is already durable in the database; nobody listening is normal,
    not an error. This must never raise."""
    broker.publish(uuid.uuid4(), {"type": "ticket_resolved"})


@pytest.mark.asyncio
async def test_leaving_the_context_unregisters_the_subscriber():
    user = uuid.uuid4()
    with broker.subscribe(user):
        assert broker.subscriber_count(user) == 1
    assert broker.subscriber_count(user) == 0


@pytest.mark.asyncio
async def test_a_subscriber_that_cannot_keep_up_is_dropped_not_allowed_to_block():
    """A stalled SSE client must never apply backpressure to the request that
    is publishing. Once its queue is full the subscriber is marked dropped and
    further events for it are discarded."""
    user = uuid.uuid4()
    with broker.subscribe(user, max_queue=2) as sub:
        for i in range(10):
            broker.publish(user, {"n": i})
        assert sub.dropped is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_notifications_broker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.notifications'`.

- [ ] **Step 3: Implement the broker**

Create `backend/app/notifications/__init__.py` as an empty file, then `backend/app/notifications/broker.py`:

```python
"""In-process fan-out for the per-user notification SSE channel (spec 10).

Deliberately has no database dependency: durability is the `notifications`
table's job, and this module's only job is telling whoever is currently
connected that something happened. That split is why `publish` to a user
with no subscribers is a no-op rather than an error -- the row is already
safe, and a user who was offline gets it from the replay on connect
instead (spec 8.3 of the phase 6 design).

`publish` is synchronous and never awaits, because it is called from
`after_commit`, which runs inside SQLAlchemy's synchronous commit path.
A subscriber whose queue is full is marked dropped rather than awaited:
a stalled browser must not apply backpressure to an admin's approval
request. The dropped client's stream closes and its next connect replays
whatever it missed from the database.

In-process means single-worker. Running this API under multiple uvicorn
workers would give each worker its own broker, and a user connected to
worker A would not see an event published on worker B. That is an
accepted limit of the spec's `an in-process broker` (spec 10), not an
oversight -- the replay on reconnect keeps the feed *correct* either way,
just not instant.
"""
from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections import defaultdict

_DEFAULT_MAX_QUEUE = 100

_subscribers: dict[uuid.UUID, set["Subscription"]] = defaultdict(set)


class Subscription:
    """One connected SSE client. Iterate it with `await sub.get()`."""

    def __init__(self, user_id: uuid.UUID, max_queue: int) -> None:
        self.user_id = user_id
        self.dropped = False
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=max_queue)

    def _offer(self, event: dict) -> None:
        if self.dropped:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped = True

    async def get(self) -> dict:
        return await self._queue.get()


@contextlib.contextmanager
def subscribe(user_id: uuid.UUID, *, max_queue: int = _DEFAULT_MAX_QUEUE):
    subscription = Subscription(user_id, max_queue)
    _subscribers[user_id].add(subscription)
    try:
        yield subscription
    finally:
        _subscribers[user_id].discard(subscription)
        if not _subscribers[user_id]:
            del _subscribers[user_id]


def publish(user_id: uuid.UUID, event: dict) -> None:
    for subscription in tuple(_subscribers.get(user_id, ())):
        subscription._offer(event)


def subscriber_count(user_id: uuid.UUID) -> int:
    return len(_subscribers.get(user_id, ()))
```

- [ ] **Step 4: Confirm `pytest-asyncio` is configured**

Run: `uv run python -c "import pytest_asyncio; print(pytest_asyncio.__version__)"`

If it is missing, add it: `uv add --dev pytest-asyncio`, then ensure `pyproject.toml` has `asyncio_mode = "auto"` or keep the explicit `@pytest.mark.asyncio` markers used above under `[tool.pytest.ini_options]`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_notifications_broker.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/notifications backend/tests/test_notifications_broker.py backend/pyproject.toml
git commit -m "Add the in-process notification broker"
```

---

### Task 3: The notification service

**Files:**
- Create: `backend/app/notifications/service.py`
- Create: `backend/tests/test_notifications_service.py`

**Interfaces:**
- Consumes: `broker.publish` (Task 2).
- Produces:
  - `notify(db, *, user_id: uuid.UUID | None, type: NotificationType, title: str, body: str, link_type: str | None = None, link_id: uuid.UUID | None = None) -> Notification | None` — stages the row, returns `None` and does nothing when `user_id is None`.
  - `list_for_user(db, user_id, *, unread_only: bool = False) -> list[Notification]`
  - `mark_read(db, user_id, notification_id) -> Notification | None`
  - Tasks 5, 6, 9 call `notify`; Task 8 calls `list_for_user` and `mark_read`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_notifications_service.py`:

```python
from __future__ import annotations

import uuid

import pytest

from app.db.models import NotificationType, Role, User
from app.notifications import broker, service


@pytest.fixture()
def user(db_session):
    """full_name is not a column on User -- do not add it. See the project's
    recorded Postgres fixture gotchas."""
    row = User(
        username=f"u{uuid.uuid4().hex[:10]}", email=f"{uuid.uuid4().hex[:10]}@northstar.example",
        full_name="Test User", password_hash="x", role=Role.EMPLOYEE, is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_notify_stages_a_row(db_session, user):
    row = service.notify(
        db_session, user_id=user.id, type=NotificationType.APPROVAL_DECIDED,
        title="Request approved", body="REQ-000001 was approved.",
        link_type="approval_request", link_id=uuid.uuid4(),
    )
    db_session.flush()
    assert row.id is not None
    assert row.read_at is None
    assert service.list_for_user(db_session, user.id) == [row]


def test_notify_for_a_guest_is_a_no_op(db_session):
    """notifications.user_id is NOT NULL and guests are deliberately not rows
    in `users` (spec 5.1), so a guest requester has no in-app channel. This
    must return None rather than raise -- decide() calls it unconditionally."""
    assert service.notify(
        db_session, user_id=None, type=NotificationType.APPROVAL_DECIDED,
        title="t", body="b",
    ) is None


def test_publish_happens_on_commit_not_before(db_session, user):
    published: list[dict] = []
    original = broker.publish
    broker.publish = lambda uid, event: published.append(event)
    try:
        service.notify(
            db_session, user_id=user.id, type=NotificationType.TICKET_ASSIGNED,
            title="Assigned", body="TCK-000001",
        )
        db_session.flush()
        assert published == [], "publishing before commit can deliver an event for a row that then rolls back"
        db_session.commit()
        assert len(published) == 1
        assert published[0]["type"] == "ticket_assigned"
        assert published[0]["title"] == "Assigned"
    finally:
        broker.publish = original


def test_nothing_is_published_when_the_transaction_rolls_back(db_session, user):
    published: list[dict] = []
    original = broker.publish
    broker.publish = lambda uid, event: published.append(event)
    try:
        service.notify(
            db_session, user_id=user.id, type=NotificationType.TICKET_CREATED,
            title="Created", body="TCK-000002",
        )
        db_session.flush()
        db_session.rollback()
        assert published == []
    finally:
        broker.publish = original


def test_mark_read_sets_read_at_and_scopes_to_the_owner(db_session, user):
    row = service.notify(
        db_session, user_id=user.id, type=NotificationType.TICKET_RESOLVED,
        title="Resolved", body="b",
    )
    db_session.flush()

    assert service.mark_read(db_session, uuid.uuid4(), row.id) is None, "another user must not be able to read someone else's notification"
    assert row.read_at is None

    marked = service.mark_read(db_session, user.id, row.id)
    assert marked.read_at is not None
    assert service.list_for_user(db_session, user.id, unread_only=True) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_notifications_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'service' from 'app.notifications'`.

- [ ] **Step 3: Implement the service**

Create `backend/app/notifications/service.py`:

```python
"""Writes and reads `notifications` rows, and arranges for the broker to
publish each one *after* its transaction commits (spec 8.2 of the phase 6
design).

The publish deliberately does not happen at the call site. Two bugs that
avoids: six different call sites each having to remember to publish, and
an SSE client being told about a notification whose transaction then rolls
back -- the event would be unretractable while the row would never exist.

Like `audit.record_audit`, `notify` stages and does not commit: the
notification belongs to the caller's transaction, so a notification can
never survive a mutation that was undone.
"""
from __future__ import annotations

import uuid

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.models import Notification, NotificationType
from app.notifications import broker

_PENDING_KEY = "_phase6_pending_notifications"


def _event_payload(row: Notification) -> dict:
    return {
        "type": row.type.value,
        "id": str(row.id),
        "title": row.title,
        "body": row.body,
        "link_type": row.link_type,
        "link_id": str(row.link_id) if row.link_id else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _flush_pending(session: Session) -> None:
    pending = session.info.pop(_PENDING_KEY, [])
    for row in pending:
        broker.publish(row.user_id, _event_payload(row))


def _discard_pending(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


@event.listens_for(Session, "after_commit")
def _publish_after_commit(session: Session) -> None:
    _flush_pending(session)


@event.listens_for(Session, "after_soft_rollback")
def _drop_after_rollback(session: Session, previous_transaction) -> None:
    _discard_pending(session)


def notify(
    db: Session,
    *,
    user_id: uuid.UUID | None,
    type: NotificationType | str,
    title: str,
    body: str,
    link_type: str | None = None,
    link_id: uuid.UUID | None = None,
) -> Notification | None:
    """Returns None for a guest (`user_id is None`) rather than raising.
    `notifications.user_id` is NOT NULL and guests are deliberately not rows
    in `users` (spec 5.1), so there is nothing to write -- and every caller
    would otherwise need the same guard. Guests hear about decisions only if
    the executed action was itself an email to them."""
    if user_id is None:
        return None

    row = Notification(
        user_id=user_id,
        type=NotificationType(type) if not isinstance(type, NotificationType) else type,
        title=title,
        body=body,
        link_type=link_type,
        link_id=link_id,
    )
    db.add(row)
    db.flush()
    db.info.setdefault(_PENDING_KEY, []).append(row)
    return row


def list_for_user(db: Session, user_id: uuid.UUID, *, unread_only: bool = False) -> list[Notification]:
    query = db.query(Notification).filter(Notification.user_id == user_id)
    if unread_only:
        query = query.filter(Notification.read_at.is_(None))
    return query.order_by(Notification.created_at.asc(), Notification.id.asc()).all()


def mark_read(db: Session, user_id: uuid.UUID, notification_id: uuid.UUID) -> Notification | None:
    """Scoped to the owner: there is no code path by which one user marks
    another user's notification read (spec 6.4's row-scoping rule applied
    to this table). Returns None when the row does not exist *or* is not
    this user's -- the caller renders both as 404 so the endpoint does not
    leak whether the id exists."""
    from datetime import datetime, timezone

    row = db.query(Notification).filter(
        Notification.id == notification_id, Notification.user_id == user_id,
    ).one_or_none()
    if row is None:
        return None
    if row.read_at is None:
        row.read_at = datetime.now(timezone.utc)
        db.flush()
    return row
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_notifications_service.py -v`
Expected: 5 passed.

If `test_nothing_is_published_when_the_transaction_rolls_back` fails, the `after_soft_rollback` listener is not firing for the savepoint-based `db_session` fixture. Fix by also clearing pending state in an `after_rollback` listener — register both, since the fixture rolls back an outer transaction while application code rolls back a session.

- [ ] **Step 5: Commit**

```bash
git add backend/app/notifications/service.py backend/tests/test_notifications_service.py
git commit -m "Add the notification service, publishing on commit"
```

---

### Task 4: Email — config, allowlist, transport, `outbound_emails`

**Files:**
- Modify: `backend/app/config.py` (add `smtp_secure`, `email_recipient_allowlist`)
- Modify: `.env.example`
- Create: `backend/app/notifications/email.py`
- Create: `backend/tests/test_notifications_email.py`

**Interfaces:**
- Consumes: `OutboundEmail.approval_status_at_send` (Task 1).
- Produces:
  - `is_allowed_recipient(address: str, patterns: list[str]) -> bool`
  - `allowlist_patterns() -> list[str]`
  - `send(db, *, approval: ApprovalRequest, to_address: str, subject: str, body: str) -> OutboundEmail`
  - `_transport` module singleton and `SmtpTransport` protocol with `send(message: EmailMessage, *, to_address: str) -> str`.
  - Task 5's `send_email` handler calls `send`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_notifications_email.py`:

```python
from __future__ import annotations

import smtplib
import uuid

import pytest

from app.db.models import (
    ApprovalActionType, ApprovalRequest, ApprovalStatus, Conversation, EmailStatus, RiskLevel,
)
from app.notifications import email as email_module


@pytest.fixture()
def approved_request(db_session):
    conv = Conversation(guest_name="Guest", guest_email="guest@northstar.example")
    db_session.add(conv)
    db_session.flush()
    request = ApprovalRequest(
        conversation_id=conv.id, task_id=None, requester_user_id=None,
        action_type=ApprovalActionType.SEND_EMAIL,
        action_payload={"to_address": "ops@northstar.example", "subject": "s", "body": "b"},
        justification="j", risk_level=RiskLevel.LOW, agent_summary="a",
        status=ApprovalStatus.APPROVED,
    )
    db_session.add(request)
    db_session.flush()
    return request


class RecordingTransport:
    def __init__(self, raises: Exception | None = None) -> None:
        self.sent: list[tuple[str, str]] = []
        self.raises = raises

    def send(self, message, *, to_address: str) -> str:
        if self.raises:
            raise self.raises
        self.sent.append((to_address, message["Subject"]))
        return "250 OK"


@pytest.fixture()
def transport(monkeypatch):
    recorder = RecordingTransport()
    monkeypatch.setattr(email_module, "_transport", recorder)
    monkeypatch.setattr(email_module, "allowlist_patterns", lambda: ["ops@northstar.example", "*@example.test"])
    return recorder


# ---- allowlist ----------------------------------------------------------

@pytest.mark.parametrize(
    "address,patterns,expected",
    [
        ("ops@northstar.example", ["ops@northstar.example"], True),
        ("OPS@NORTHSTAR.EXAMPLE", ["ops@northstar.example"], True),
        ("other@northstar.example", ["ops@northstar.example"], False),
        ("anyone@example.test", ["*@example.test"], True),
        ("anyone@evil.test", ["*@example.test"], False),
        ("ops@northstar.example", [], False),
        ("", ["*@example.test"], False),
    ],
)
def test_allowlist_matching(address, patterns, expected):
    assert email_module.is_allowed_recipient(address, patterns) is expected


def test_empty_allowlist_fails_closed():
    """A missing config value must never widen the blast radius."""
    assert email_module.is_allowed_recipient("anyone@anywhere.test", []) is False


# ---- send ---------------------------------------------------------------

def test_send_to_an_allowlisted_address_records_sent(db_session, approved_request, transport):
    row = email_module.send(
        db_session, approval=approved_request,
        to_address="ops@northstar.example", subject="Subject", body="Body",
    )
    assert row.status is EmailStatus.SENT
    assert row.sent_at is not None
    assert row.smtp_response == "250 OK"
    assert row.approval_status_at_send is ApprovalStatus.APPROVED
    assert transport.sent == [("ops@northstar.example", "Subject")]


def test_a_non_allowlisted_recipient_is_rejected_but_still_recorded(db_session, approved_request, transport):
    """A rejection that leaves no trace is less auditable than one that does."""
    row = email_module.send(
        db_session, approval=approved_request,
        to_address="stranger@elsewhere.test", subject="Subject", body="Body",
    )
    assert row.status is EmailStatus.FAILED
    assert row.smtp_response == "recipient not allowlisted"
    assert row.sent_at is None
    assert transport.sent == [], "the socket must never open for a non-allowlisted recipient"


def test_a_pending_approval_is_refused_before_any_row_is_written(db_session, approved_request, transport):
    """Belt to the database's braces: the DB constraint would reject this
    anyway, but failing here gives a readable error instead of an
    IntegrityError from deep inside a flush."""
    approved_request.status = ApprovalStatus.PENDING
    db_session.flush()
    with pytest.raises(email_module.ApprovalNotGranted):
        email_module.send(
            db_session, approval=approved_request,
            to_address="ops@northstar.example", subject="s", body="b",
        )


def test_the_row_exists_even_when_the_socket_fails(db_session, approved_request, monkeypatch):
    """Spec 9.3: every attempt writes a row before the socket opens, so a
    crash mid-send is still visible."""
    monkeypatch.setattr(email_module, "allowlist_patterns", lambda: ["ops@northstar.example"])
    monkeypatch.setattr(
        email_module, "_transport", RecordingTransport(raises=smtplib.SMTPException("boom")),
    )
    row = email_module.send(
        db_session, approval=approved_request,
        to_address="ops@northstar.example", subject="s", body="b",
    )
    assert row.status is EmailStatus.FAILED
    assert "boom" in row.smtp_response
    assert row.id is not None


def test_authentication_failure_is_not_retried(db_session, approved_request, monkeypatch):
    """A bad credential is not a transient fault (spec 9.3)."""
    attempts = []

    class CountingTransport:
        def send(self, message, *, to_address: str) -> str:
            attempts.append(to_address)
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(email_module, "allowlist_patterns", lambda: ["ops@northstar.example"])
    monkeypatch.setattr(email_module, "_transport", CountingTransport())

    row = email_module.send(
        db_session, approval=approved_request,
        to_address="ops@northstar.example", subject="s", body="b",
    )
    assert row.status is EmailStatus.FAILED
    assert len(attempts) == 1


# ---- transport selection ------------------------------------------------

@pytest.mark.parametrize(
    "port,secure,expected",
    [(465, False, "ssl"), (465, True, "ssl"), (587, True, "ssl"), (587, False, "starttls"), (25, False, "starttls")],
)
def test_transport_mode_selection(port, secure, expected):
    """Spec amendment 2.4: the configured Gmail account is implicit TLS on
    465 while spec 9.3 assumes STARTTLS on 587. Both must work."""
    assert email_module.transport_mode(port=port, secure=secure) == expected
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_notifications_email.py -v`
Expected: FAIL — `ImportError: cannot import name 'email' from 'app.notifications'`.

- [ ] **Step 3: Add the config fields**

In `backend/app/config.py`, inside `Settings`, immediately after `smtp_from`:

```python
    # True selects implicit TLS (SMTP_SSL); false selects STARTTLS. Port 465
    # implies implicit TLS regardless, because no server speaks STARTTLS
    # there. Spec 9.3 assumes 587/STARTTLS; the configured account is
    # 465/implicit. Supporting both is amendment 2.4 of the phase 6 design.
    smtp_secure: bool = False
    # Comma-separated glob patterns. EMPTY MEANS SEND TO NOBODY -- this fails
    # closed on purpose, so a missing config value can never widen the blast
    # radius of an approved send_email action.
    email_recipient_allowlist: str = ""
```

In `.env.example`, under the SMTP block:

```
SMTP_SECURE=false
# Comma-separated glob patterns. Empty rejects every recipient (fail closed).
# The seeded dataset uses @northstar.example, which does not resolve, so demo
# sends fail loudly and are recorded as failed. Add a real address here to
# actually deliver.
EMAIL_RECIPIENT_ALLOWLIST=*@northstar.example
```

- [ ] **Step 4: Implement the email module**

Create `backend/app/notifications/email.py`:

```python
"""The only write path to `outbound_emails` (spec 9.3).

Three rules this module exists to keep:

1. The row is written BEFORE the socket opens, so a crash mid-send is still
   visible afterwards.
2. `approval_status_at_send` is set from the approval's real status, which
   is what lets the database enforce spec 5.3's invariant. Never hardcode
   it -- the composite FK will reject a forged value.
3. The recipient is checked against a configured allowlist, and an empty
   allowlist rejects everyone. A rejected recipient is still recorded, as
   `failed`: a rejection that leaves no trace is less auditable than one
   that does.

The transport is a module-level singleton so tests can replace it wholesale,
matching the `_anthropic_client` seam in app/chat/router.py. It is called
synchronously and blocks; every caller reaches it from a sync FastAPI
endpoint, which Starlette runs in a threadpool, so the event loop -- and
therefore every open SSE stream -- keeps running during a send.
"""
from __future__ import annotations

import fnmatch
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ApprovalRequest, ApprovalStatus, EmailStatus, OutboundEmail

_SEND_TIMEOUT_SECONDS = 10

# The approval states from which a send is legitimate. Mirrors the database
# CHECK in the phase 6 migration; keep the two in step.
_SENDABLE_STATUSES = frozenset({ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED})


class ApprovalNotGranted(RuntimeError):
    """Raised instead of letting the database's composite FK reject the
    insert, so the failure reads as a policy violation rather than as an
    IntegrityError from inside a flush."""


def allowlist_patterns() -> list[str]:
    raw = get_settings().email_recipient_allowlist
    return [p.strip() for p in raw.split(",") if p.strip()]


def is_allowed_recipient(address: str, patterns: list[str]) -> bool:
    if not address or not patterns:
        return False
    candidate = address.strip().lower()
    return any(fnmatch.fnmatch(candidate, p.strip().lower()) for p in patterns)


def transport_mode(*, port: int, secure: bool) -> str:
    """Port 465 is implicit TLS by definition -- no server speaks STARTTLS
    there -- so it wins over a false `secure` flag rather than producing a
    connection that hangs."""
    return "ssl" if secure or port == 465 else "starttls"


class SmtplibTransport:
    def send(self, message: EmailMessage, *, to_address: str) -> str:
        settings = get_settings()
        mode = transport_mode(port=settings.smtp_port, secure=settings.smtp_secure)
        if mode == "ssl":
            client = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=_SEND_TIMEOUT_SECONDS)
        else:
            client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=_SEND_TIMEOUT_SECONDS)
        with client:
            if mode == "starttls":
                client.starttls()
            if settings.smtp_user:
                # No retry: SMTPAuthenticationError propagates to send(),
                # which records `failed`. A bad credential is not transient.
                client.login(settings.smtp_user, settings.smtp_password)
            response = client.send_message(message, to_addrs=[to_address])
        return "250 OK" if not response else str(response)


_transport: object = SmtplibTransport()


def send(
    db: Session,
    *,
    approval: ApprovalRequest,
    to_address: str,
    subject: str,
    body: str,
) -> OutboundEmail:
    if approval.status not in _SENDABLE_STATUSES:
        raise ApprovalNotGranted(
            f"approval {approval.id} is {approval.status.value!r}; "
            "an email may be sent only from 'approved' or 'executed'"
        )

    row = OutboundEmail(
        approval_request_id=approval.id,
        approval_status_at_send=approval.status,
        to_address=to_address,
        subject=subject,
        body=body,
        status=EmailStatus.QUEUED,
    )
    db.add(row)
    db.flush()

    if not is_allowed_recipient(to_address, allowlist_patterns()):
        row.status = EmailStatus.FAILED
        row.smtp_response = "recipient not allowlisted"
        db.flush()
        return row

    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    try:
        row.smtp_response = _transport.send(message, to_address=to_address)
        row.status = EmailStatus.SENT
        row.sent_at = datetime.now(timezone.utc)
    except Exception as exc:  # noqa: BLE001 -- every failure is recorded, never raised
        row.status = EmailStatus.FAILED
        row.smtp_response = f"{type(exc).__name__}: {exc}"
    db.flush()
    return row
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_notifications_email.py -v`
Expected: 18 passed.

- [ ] **Step 6: Add the config test**

Append to `backend/tests/test_config.py`:

```python
def test_email_allowlist_defaults_to_empty_meaning_nobody():
    """Fail closed: an unset EMAIL_RECIPIENT_ALLOWLIST must reject every
    recipient rather than allow every recipient."""
    from app.config import Settings

    settings = Settings(_env_file=None)
    assert settings.email_recipient_allowlist == ""
    assert settings.smtp_secure is False
```

- [ ] **Step 7: Run the config test**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/config.py backend/app/notifications/email.py backend/tests/test_notifications_email.py backend/tests/test_config.py .env.example
git commit -m "Add the email path: allowlist, transport selection, outbound_emails"
```

---

### Task 5: Executor — registry, re-validation, re-authorization, all seven handlers

**Files:**
- Create: `backend/app/approvals/executor.py`
- Create: `backend/tests/test_approvals_executor.py`

**Interfaces:**
- Consumes: `RunTrigger.APPROVAL_EXECUTION` (Task 1), `email.send` (Task 4), `notifications.service.notify` (Task 3), `tickets.service.reassign`, `chat.service.append_message`.
- Produces:
  - `execute(db, approval: ApprovalRequest) -> ExecutionOutcome` — untraced logic, what unit tests call
  - `execute_traced(db, approval) -> ExecutionOutcome` — same, wrapped in the `executor` span; requires an active run
  - `ExecutionOutcome` dataclass: `ok: bool`, `result: dict`
  - `HANDLERS: dict[ApprovalActionType, Handler]`
  - `PAYLOAD_SCHEMAS: dict[ApprovalActionType, type[BaseModel]]`
  - `HANDLERS` complete for all seven action types. Task 6's `decide()` calls `execute_traced`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_approvals_executor.py`:

```python
from __future__ import annotations

import uuid

import pytest

from app.approvals import executor
from app.db.models import (
    ApprovalActionType, ApprovalRequest, ApprovalStatus, Conversation, RiskLevel, Role, User,
)


@pytest.fixture()
def requester(db_session):
    row = User(
        username=f"u{uuid.uuid4().hex[:10]}", email=f"{uuid.uuid4().hex[:10]}@northstar.example",
        full_name="Test User", password_hash="x", role=Role.EMPLOYEE, is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture()
def make_approval(db_session, requester):
    def _make(action_type: ApprovalActionType, payload: dict, *, requester_user_id=...):
        conv = Conversation(guest_name="Guest", guest_email="guest@northstar.example")
        db_session.add(conv)
        db_session.flush()
        row = ApprovalRequest(
            conversation_id=conv.id, task_id=None,
            requester_user_id=requester.id if requester_user_id is ... else requester_user_id,
            action_type=action_type, action_payload=payload,
            justification="j", risk_level=RiskLevel.LOW, agent_summary="a",
            status=ApprovalStatus.APPROVED,
        )
        db_session.add(row)
        db_session.flush()
        return row
    return _make


def test_every_action_type_has_a_handler():
    """Adding a member to ApprovalActionType without a handler must fail the
    suite rather than surface as a runtime KeyError on an approved request."""
    missing = [a.value for a in ApprovalActionType if a not in executor.HANDLERS]
    assert missing == []


def test_every_action_type_has_a_payload_schema():
    missing = [a.value for a in ApprovalActionType if a not in executor.PAYLOAD_SCHEMAS]
    assert missing == []


def test_a_simulated_action_reports_itself_as_simulated(db_session, make_approval):
    """This system has no external IT infrastructure. A simulated grant must
    be impossible to mistake for a real one."""
    approval = make_approval(
        ApprovalActionType.GRANT_SYSTEM_ACCESS,
        {"system": "kubernetes-prod", "target_username": "someone", "access_level": "read"},
    )
    outcome = executor.execute(db_session, approval)
    assert outcome.ok is True
    assert outcome.result["simulated"] is True
    assert outcome.result["absent_system"]


@pytest.mark.parametrize("action,payload", [
    (ApprovalActionType.GRANT_SYSTEM_ACCESS, {"system": "s", "target_username": "u", "access_level": "read"}),
    (ApprovalActionType.RESET_CREDENTIAL, {"target_username": "u", "credential_kind": "password"}),
    (ApprovalActionType.EXTERNAL_API_WRITE, {"endpoint": "https://x.test/y", "method": "POST", "payload": {}}),
])
def test_all_three_simulated_actions_succeed_and_are_marked(db_session, make_approval, action, payload):
    outcome = executor.execute(db_session, make_approval(action, payload))
    assert outcome.ok is True
    assert outcome.result["simulated"] is True


def test_a_payload_that_no_longer_validates_fails_without_side_effects(db_session, make_approval):
    """Spec 9.2: an approval is permission for the action AS DESCRIBED."""
    approval = make_approval(ApprovalActionType.GRANT_SYSTEM_ACCESS, {"system": "s"})  # missing fields
    outcome = executor.execute(db_session, approval)
    assert outcome.ok is False
    assert outcome.result["reason"] == "payload_invalid"
    assert "target_username" in outcome.result["detail"]


def test_a_deactivated_requester_fails_execution(db_session, make_approval, requester):
    """An approval is not a bypass of policy. If the requester has since been
    deactivated, the action must not run on their behalf."""
    approval = make_approval(
        ApprovalActionType.RESET_CREDENTIAL, {"target_username": "u", "credential_kind": "password"},
    )
    requester.is_active = False
    db_session.flush()

    outcome = executor.execute(db_session, approval)
    assert outcome.ok is False
    assert outcome.result["reason"] == "requester_not_active"


def test_a_missing_requester_fails_execution(db_session, make_approval):
    approval = make_approval(
        ApprovalActionType.RESET_CREDENTIAL, {"target_username": "u", "credential_kind": "password"},
        requester_user_id=uuid.uuid4(),
    )
    outcome = executor.execute(db_session, approval)
    assert outcome.ok is False
    assert outcome.result["reason"] == "requester_not_found"


def test_a_guest_requester_is_permitted(db_session, make_approval):
    """requester_user_id IS NULL means a guest, which is legitimate -- the
    agent files approval requests in guest conversations too."""
    approval = make_approval(
        ApprovalActionType.RESET_CREDENTIAL, {"target_username": "u", "credential_kind": "password"},
        requester_user_id=None,
    )
    outcome = executor.execute(db_session, approval)
    assert outcome.ok is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_approvals_executor.py -v`
Expected: FAIL — `ImportError: cannot import name 'executor' from 'app.approvals'`.

- [ ] **Step 3: Implement the executor**

Create `backend/app/approvals/executor.py`:

```python
"""Executes an approved action (spec 9.2).

Lives in app/approvals/ rather than app/agent/ -- layout section 16 lists
executor.py under agent/, but section 9.2 says `approvals.execute()`
dispatches to it, and it executes admin-approved actions, not agent tool
calls. Amendment 2.1 of the phase 6 design records the deviation.

Three of the seven action types have no target in this system: there is no
external IT infrastructure to grant access on, no credential store to reset
against, and no external API to write to. Those record an explicitly
simulated result, so nothing downstream can read a simulation as a real
grant. The other four act for real, against our own data.

Everything here is synchronous. `tracing.span`'s context-manager form is
async-only, so the decorator form is used, and the whole path is reached
from a sync FastAPI endpoint that Starlette runs in a threadpool -- which
is also what keeps a blocking 10-second SMTP send from stalling every open
SSE stream.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.db.models import ApprovalActionType, ApprovalRequest, Role, SpanKind, User
from app.rbac.policy import Deny, Principal, authorize
from app.tracing.spans import span


@dataclass
class ExecutionOutcome:
    ok: bool
    result: dict


# ---- payload schemas ---------------------------------------------------
# Re-validated at execution time, not merely at request time: spec 9.2's
# "if the payload changed ... execution fails with a recorded reason".

class SendEmailPayload(BaseModel):
    to_address: str
    subject: str
    body: str


class GrantSystemAccessPayload(BaseModel):
    system: str
    target_username: str
    access_level: str


class ResetCredentialPayload(BaseModel):
    target_username: str
    credential_kind: str


class UpdateUserClearancePayload(BaseModel):
    target_username: str
    new_clearance: str


class DiscloseRestrictedInformationPayload(BaseModel):
    disclosure: str


class CrossDepartmentTicketAssignmentPayload(BaseModel):
    ticket_id: str
    assignee_helpdesk_ref: str
    rationale: str


class ExternalApiWritePayload(BaseModel):
    endpoint: str
    method: str
    payload: dict


PAYLOAD_SCHEMAS: dict[ApprovalActionType, type[BaseModel]] = {
    ApprovalActionType.SEND_EMAIL: SendEmailPayload,
    ApprovalActionType.GRANT_SYSTEM_ACCESS: GrantSystemAccessPayload,
    ApprovalActionType.RESET_CREDENTIAL: ResetCredentialPayload,
    ApprovalActionType.UPDATE_USER_CLEARANCE: UpdateUserClearancePayload,
    ApprovalActionType.DISCLOSE_RESTRICTED_INFORMATION: DiscloseRestrictedInformationPayload,
    ApprovalActionType.CROSS_DEPARTMENT_TICKET_ASSIGNMENT: CrossDepartmentTicketAssignmentPayload,
    ApprovalActionType.EXTERNAL_API_WRITE: ExternalApiWritePayload,
}


# ---- simulated handlers ------------------------------------------------

def _simulate(absent_system: str):
    def handler(db: Session, approval: ApprovalRequest, payload: BaseModel) -> dict:
        return {
            "simulated": True,
            "absent_system": absent_system,
            "would_have": payload.model_dump(),
        }
    return handler


Handler = Callable[[Session, ApprovalRequest, BaseModel], dict]

HANDLERS: dict[ApprovalActionType, Handler] = {
    ApprovalActionType.GRANT_SYSTEM_ACCESS: _simulate("identity provider / target system"),
    ApprovalActionType.RESET_CREDENTIAL: _simulate("credential store"),
    ApprovalActionType.EXTERNAL_API_WRITE: _simulate("external API"),
}


# ---- re-validation and re-authorization (spec 9.2) ---------------------

def _rebuild_principal(db: Session, approval: ApprovalRequest) -> Principal | ExecutionOutcome:
    """Rebuilds the requester's principal from CURRENT database state, never
    from anything captured when the request was filed. A user demoted or
    deactivated between filing and approval must not have the action run on
    their behalf."""
    if approval.requester_user_id is None:
        return Principal(
            kind="guest", user_id=None, role="guest", clearance=None,
            department=None, employee_ref=None, helpdesk_ref=None,
        )

    user = db.query(User).filter(User.id == approval.requester_user_id).one_or_none()
    if user is None:
        return ExecutionOutcome(False, {"reason": "requester_not_found", "detail": str(approval.requester_user_id)})
    if not user.is_active:
        return ExecutionOutcome(False, {"reason": "requester_not_active", "detail": user.username})

    return Principal(
        kind="user", user_id=str(user.id), role=user.role.value,
        clearance=user.clearance.value if user.clearance else None,
        department=user.department, employee_ref=user.employee_ref,
        helpdesk_ref=user.helpdesk_ref,
    )


def execute(db: Session, approval: ApprovalRequest) -> ExecutionOutcome:
    """Deliberately NOT decorated with @span. `tracing.spans._ActiveSpan.enter`
    raises RuntimeError when there is no active run, so a decorated `execute`
    would be uncallable from a unit test that has no reason to own a run.
    `execute_traced` below is the production entry point and carries the
    span spec 9.2 requires; this function is the logic, testable on its own."""
    principal_or_failure = _rebuild_principal(db, approval)
    if isinstance(principal_or_failure, ExecutionOutcome):
        return principal_or_failure
    principal = principal_or_failure

    schema = PAYLOAD_SCHEMAS[approval.action_type]
    try:
        payload = schema.model_validate(approval.action_payload)
    except ValidationError as exc:
        return ExecutionOutcome(False, {"reason": "payload_invalid", "detail": exc.errors(include_url=False)})

    # Spec 9.2 requires re-running policy for the original requester. Be
    # honest about what this currently buys: rbac.authorize is role-based
    # only -- it ignores its `arguments` parameter, and
    # create_approval_request is not in _GUEST_DENIED_TOOLS -- so this
    # catches a requester whose ROLE changed and nothing finer. The real
    # protection above is the reload-and-revalidate. This step is here
    # because the spec requires it and because it starts doing genuine work
    # the moment argument-level rules land. Do not describe it as a
    # payload-level policy check.
    decision = authorize(principal, "create_approval_request", dict(approval.action_payload))
    if isinstance(decision, Deny):
        return ExecutionOutcome(False, {"reason": "policy_denied", "detail": decision.reason})

    handler = HANDLERS[approval.action_type]
    try:
        return ExecutionOutcome(True, handler(db, approval, payload))
    except Exception as exc:  # noqa: BLE001 -- recorded on the approval, never raised at the admin
        return ExecutionOutcome(False, {"reason": "handler_failed", "detail": f"{type(exc).__name__}: {exc}"})


@span(SpanKind.EXECUTOR, "approval.execute")
def execute_traced(db: Session, approval: ApprovalRequest) -> ExecutionOutcome:
    """The production entry point: identical to `execute` but wrapped in the
    `executor` span spec 9.2 requires. Requires an active run -- `decide()`
    starts one. The sync decorator form is used because `span`'s
    context-manager form is async-only and this whole path is sync."""
    return execute(db, approval)
```

- [ ] **Step 4: Check `Deny`'s attribute name and `Principal`'s fields**

Run: `uv run python -c "import inspect, app.rbac.policy as p; print(inspect.getsource(p.Deny)); print(inspect.getsource(p.Principal))"`

Adjust `decision.reason` and the `Principal(...)` constructor calls above to match the real field names exactly. Do not guess.

- [ ] **Step 5: Do NOT commit yet — the handler-completeness test is still red**

Run: `uv run pytest tests/test_approvals_executor.py -v`
Expected: `test_every_action_type_has_a_handler` FAILS listing the four real actions; every other test passes. Steps 6-10 below add those four handlers. Nothing is committed until the whole file is green — this task's deliverable is a complete executor, not a registry with three of seven entries.

- [ ] **Step 6: Write the failing tests for the four real handlers**

Append to `backend/tests/test_approvals_executor.py`:

```python
from app.db.models import Clearance, Message, MessageRole, NotificationType, Notification
from app.notifications import email as email_module


def test_send_email_handler_delivers_and_records(db_session, make_approval, monkeypatch):
    sent = []

    class T:
        def send(self, message, *, to_address: str) -> str:
            sent.append(to_address)
            return "250 OK"

    monkeypatch.setattr(email_module, "_transport", T())
    monkeypatch.setattr(email_module, "allowlist_patterns", lambda: ["ops@northstar.example"])

    approval = make_approval(
        ApprovalActionType.SEND_EMAIL,
        {"to_address": "ops@northstar.example", "subject": "Subject", "body": "Body"},
    )
    outcome = executor.execute(db_session, approval)
    assert outcome.ok is True
    assert outcome.result["email_status"] == "sent"
    assert sent == ["ops@northstar.example"]


def test_send_email_records_a_failure_as_a_failed_outcome(db_session, make_approval, monkeypatch):
    """A non-allowlisted recipient is a failed execution, not a successful
    one that quietly sent nothing."""
    monkeypatch.setattr(email_module, "allowlist_patterns", lambda: ["ops@northstar.example"])
    approval = make_approval(
        ApprovalActionType.SEND_EMAIL,
        {"to_address": "stranger@elsewhere.test", "subject": "s", "body": "b"},
    )
    outcome = executor.execute(db_session, approval)
    assert outcome.ok is False
    assert outcome.result["email_status"] == "failed"
    assert outcome.result["smtp_response"] == "recipient not allowlisted"


def test_update_user_clearance_writes_the_column(db_session, make_approval, requester):
    approval = make_approval(
        ApprovalActionType.UPDATE_USER_CLEARANCE,
        {"target_username": requester.username, "new_clearance": "sensitive"},
    )
    outcome = executor.execute(db_session, approval)
    db_session.flush()
    assert outcome.ok is True
    assert requester.clearance is Clearance.SENSITIVE


def test_update_user_clearance_rejects_an_unknown_clearance(db_session, make_approval, requester):
    approval = make_approval(
        ApprovalActionType.UPDATE_USER_CLEARANCE,
        {"target_username": requester.username, "new_clearance": "godmode"},
    )
    outcome = executor.execute(db_session, approval)
    assert outcome.ok is False
    assert outcome.result["reason"] in {"payload_invalid", "handler_failed"}


def test_update_user_clearance_on_a_missing_user_fails(db_session, make_approval):
    approval = make_approval(
        ApprovalActionType.UPDATE_USER_CLEARANCE,
        {"target_username": "nobody-at-all", "new_clearance": "standard"},
    )
    outcome = executor.execute(db_session, approval)
    assert outcome.ok is False


def test_disclose_posts_a_system_message_attributed_to_the_admin(db_session, make_approval, requester):
    """Spec 9.2: a cross-clearance question becomes a workflow, and the
    disclosure is attributed to the approving admin."""
    approval = make_approval(
        ApprovalActionType.DISCLOSE_RESTRICTED_INFORMATION,
        {"disclosure": "The build server root password rotation is on Fridays."},
    )
    approval.decided_by_user_id = requester.id
    db_session.flush()

    outcome = executor.execute(db_session, approval)
    assert outcome.ok is True

    messages = db_session.query(Message).filter(
        Message.conversation_id == approval.conversation_id,
        Message.role == MessageRole.SYSTEM,
    ).all()
    assert len(messages) == 1
    rendered = str(messages[0].content)
    assert "rotation is on Fridays" in rendered
    assert requester.username in rendered


def test_cross_department_assignment_reassigns_the_ticket(db_session, make_approval, make_ticket):
    ticket = make_ticket(assignee_helpdesk_ref="HD-901")
    approval = make_approval(
        ApprovalActionType.CROSS_DEPARTMENT_TICKET_ASSIGNMENT,
        {"ticket_id": str(ticket.id), "assignee_helpdesk_ref": "HD-005", "rationale": "VPN specialist"},
    )
    outcome = executor.execute(db_session, approval)
    db_session.flush()
    assert outcome.ok is True
    assert ticket.assignee_helpdesk_ref == "HD-005"
    assert "VPN specialist" in ticket.assignment_rationale


def test_cross_department_assignment_on_a_missing_ticket_fails(db_session, make_approval):
    approval = make_approval(
        ApprovalActionType.CROSS_DEPARTMENT_TICKET_ASSIGNMENT,
        {"ticket_id": str(uuid.uuid4()), "assignee_helpdesk_ref": "HD-005", "rationale": "r"},
    )
    outcome = executor.execute(db_session, approval)
    assert outcome.ok is False
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `uv run pytest tests/test_approvals_executor.py -v`
Expected: the new tests FAIL with `KeyError: <ApprovalActionType.SEND_EMAIL...>`.

- [ ] **Step 8: Implement the four real handlers**

In `backend/app/approvals/executor.py`, add these imports at the top:

```python
import uuid as _uuid

from app.db.models import Clearance, MessageRole, NotificationType, Ticket
```

Then add the handlers above the `HANDLERS` dict and register them:

```python
def _handle_send_email(db: Session, approval: ApprovalRequest, payload: SendEmailPayload) -> dict:
    """Imported here rather than at module scope: app.notifications.email
    imports app.config, and a module-scope import would make importing the
    executor require SMTP configuration to be present."""
    from app.notifications import email as email_module

    row = email_module.send(
        db, approval=approval, to_address=payload.to_address,
        subject=payload.subject, body=payload.body,
    )
    return {
        "email_id": str(row.id),
        "email_status": row.status.value,
        "to_address": row.to_address,
        "smtp_response": row.smtp_response,
    }


def _handle_update_user_clearance(db: Session, approval: ApprovalRequest, payload: UpdateUserClearancePayload) -> dict:
    target = db.query(User).filter(User.username == payload.target_username).one_or_none()
    if target is None:
        raise LookupError(f"no user named {payload.target_username!r}")
    previous = target.clearance.value if target.clearance else None
    target.clearance = Clearance(payload.new_clearance)
    db.flush()
    return {
        "target_username": target.username,
        "previous_clearance": previous,
        "new_clearance": target.clearance.value,
    }


def _handle_disclose_restricted_information(
    db: Session, approval: ApprovalRequest, payload: DiscloseRestrictedInformationPayload,
) -> dict:
    """Spec 9.2: the disclosure is delivered to the user as a system message
    in the conversation, attributed to the approving admin. Attribution is
    the point -- an unattributed disclosure is indistinguishable from the
    agent having decided to answer on its own."""
    from app.chat.service import append_message

    admin_name = "an administrator"
    if approval.decided_by_user_id:
        admin = db.query(User).filter(User.id == approval.decided_by_user_id).one_or_none()
        if admin:
            admin_name = admin.username

    text = (
        f"Approved disclosure (REQ-{approval.request_number:06d}), released by {admin_name}:\n\n"
        f"{payload.disclosure}"
    )
    message = append_message(
        db, approval.conversation_id, MessageRole.SYSTEM, [{"type": "text", "text": text}],
    )
    return {"message_id": str(message.id), "attributed_to": admin_name}


def _handle_cross_department_ticket_assignment(
    db: Session, approval: ApprovalRequest, payload: CrossDepartmentTicketAssignmentPayload,
) -> dict:
    from app.tickets.service import reassign

    ticket = db.query(Ticket).filter(Ticket.id == _uuid.UUID(payload.ticket_id)).one_or_none()
    if ticket is None:
        raise LookupError(f"no ticket with id {payload.ticket_id}")

    previous = ticket.assignee_helpdesk_ref
    # Deliberately does NOT notify here. Task 9 makes tickets.service.reassign
    # the single owner of the TICKET_ASSIGNED trigger, so every reassignment
    # notifies exactly once regardless of who initiated it. Emitting here as
    # well would send the assignee two identical notifications.
    reassign(db, ticket, assignee_helpdesk_ref=payload.assignee_helpdesk_ref, rationale=payload.rationale)
    db.flush()
    return {
        "ticket_id": str(ticket.id),
        "previous_assignee": previous,
        "new_assignee": ticket.assignee_helpdesk_ref,
    }
```

Then extend the registry:

```python
HANDLERS: dict[ApprovalActionType, Handler] = {
    ApprovalActionType.SEND_EMAIL: _handle_send_email,
    ApprovalActionType.UPDATE_USER_CLEARANCE: _handle_update_user_clearance,
    ApprovalActionType.DISCLOSE_RESTRICTED_INFORMATION: _handle_disclose_restricted_information,
    ApprovalActionType.CROSS_DEPARTMENT_TICKET_ASSIGNMENT: _handle_cross_department_ticket_assignment,
    ApprovalActionType.GRANT_SYSTEM_ACCESS: _simulate("identity provider / target system"),
    ApprovalActionType.RESET_CREDENTIAL: _simulate("credential store"),
    ApprovalActionType.EXTERNAL_API_WRITE: _simulate("external API"),
}
```

- [ ] **Step 9: Make a failed email a failed execution**

`_handle_send_email` returns normally even when the send failed, which would record `executed`. In `execute()`, replace the handler call with:

```python
    handler = HANDLERS[approval.action_type]
    try:
        result = handler(db, approval, payload)
    except Exception as exc:  # noqa: BLE001 -- recorded on the approval, never raised at the admin
        return ExecutionOutcome(False, {"reason": "handler_failed", "detail": f"{type(exc).__name__}: {exc}"})

    # An email that did not leave the building is a failed execution, not a
    # successful one that quietly sent nothing.
    if result.get("email_status") == "failed":
        return ExecutionOutcome(False, result)
    return ExecutionOutcome(True, result)
```

- [ ] **Step 10: Run the tests to verify they pass**

Run: `uv run pytest tests/test_approvals_executor.py -v`
Expected: ALL pass, including `test_every_action_type_has_a_handler`. Nothing in this file may be red at commit.

- [ ] **Step 11: Commit**

```bash
git add backend/app/approvals/executor.py backend/tests/test_approvals_executor.py
git commit -m "Add the approval executor: re-validation, re-authorization, all seven handlers"
```

---

### Task 6: `approvals.service.decide()`

**Files:**
- Modify: `backend/app/approvals/service.py`
- Modify: `backend/tests/test_approvals_service.py`

**Interfaces:**
- Consumes: `executor.execute_traced` (Task 5), `notifications.service.notify` (Task 3), `audit.record_audit`.
- Produces:
  - `decide(db, principal: Principal, request_id: uuid.UUID, *, approve: bool, note: str = "") -> ApprovalRequest`
  - `NotPending(RuntimeError)`
  - `get(db, request_id) -> ApprovalRequest | None`
  - `list_for_admin(db, *, status: ApprovalStatus | None = None) -> list[ApprovalRequest]`
  - Task 7's router calls all four.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_approvals_service.py`:

```python
import uuid as _uuid

import pytest

from app.approvals import service as approvals_service
from app.db.models import (
    ApprovalActionType, ApprovalRequest, ApprovalStatus, AuditLog, Conversation,
    Notification, NotificationType, RiskLevel, Role, User,
)
from app.rbac.policy import Principal


@pytest.fixture()
def admin_principal(db_session):
    admin = User(
        username=f"a{_uuid.uuid4().hex[:10]}", email=f"{_uuid.uuid4().hex[:10]}@northstar.example",
        full_name="Test User", password_hash="x", role=Role.ADMIN, is_active=True,
    )
    db_session.add(admin)
    db_session.flush()
    return admin, Principal(
        kind="user", user_id=str(admin.id), role="admin", clearance=None,
        department=None, employee_ref=None, helpdesk_ref=None,
    )


@pytest.fixture()
def pending_request(db_session):
    requester = User(
        username=f"u{_uuid.uuid4().hex[:10]}", email=f"{_uuid.uuid4().hex[:10]}@northstar.example",
        full_name="Test User", password_hash="x", role=Role.EMPLOYEE, is_active=True,
    )
    db_session.add(requester)
    conv = Conversation(guest_name="G", guest_email="g@northstar.example")
    db_session.add(conv)
    db_session.flush()
    row = ApprovalRequest(
        conversation_id=conv.id, task_id=None, requester_user_id=requester.id,
        action_type=ApprovalActionType.RESET_CREDENTIAL,
        action_payload={"target_username": "someone", "credential_kind": "password"},
        justification="j", risk_level=RiskLevel.LOW, agent_summary="a",
        status=ApprovalStatus.PENDING,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_denying_records_the_decision_and_notifies_without_executing(db_session, admin_principal, pending_request):
    admin, principal = admin_principal
    result = approvals_service.decide(
        db_session, principal, pending_request.id, approve=False, note="Not justified.",
    )
    assert result.status is ApprovalStatus.DENIED
    assert result.decided_by_user_id == admin.id
    assert result.decided_at is not None
    assert result.decision_note == "Not justified."
    assert result.executed_at is None
    assert result.execution_result is None

    notifications = db_session.query(Notification).filter(
        Notification.user_id == pending_request.requester_user_id,
        Notification.type == NotificationType.APPROVAL_DECIDED,
    ).all()
    assert len(notifications) == 1
    assert "denied" in notifications[0].body.lower()


def test_approving_executes_and_records_the_result(db_session, admin_principal, pending_request):
    admin, principal = admin_principal
    result = approvals_service.decide(db_session, principal, pending_request.id, approve=True, note="OK")
    assert result.status is ApprovalStatus.EXECUTED
    assert result.executed_at is not None
    assert result.execution_result["simulated"] is True


def test_a_failed_execution_lands_in_failed_with_the_reason(db_session, admin_principal, pending_request):
    pending_request.action_payload = {"target_username": "someone"}  # credential_kind missing
    db_session.flush()
    _admin, principal = admin_principal

    result = approvals_service.decide(db_session, principal, pending_request.id, approve=True, note="")
    assert result.status is ApprovalStatus.FAILED
    assert result.execution_result["reason"] == "payload_invalid"


def test_deciding_a_non_pending_request_is_refused(db_session, admin_principal, pending_request):
    """The idempotency guard: a re-submitted approval must never execute the
    action a second time."""
    _admin, principal = admin_principal
    approvals_service.decide(db_session, principal, pending_request.id, approve=True, note="")

    with pytest.raises(approvals_service.NotPending):
        approvals_service.decide(db_session, principal, pending_request.id, approve=True, note="")


def test_every_decision_writes_an_audit_row(db_session, admin_principal, pending_request):
    _admin, principal = admin_principal
    approvals_service.decide(db_session, principal, pending_request.id, approve=False, note="")
    rows = db_session.query(AuditLog).filter(
        AuditLog.target_type == "approval_request",
        AuditLog.target_id == str(pending_request.id),
    ).all()
    assert len(rows) == 1
    assert rows[0].action == "approval.decide"


def test_a_guest_requester_gets_no_notification_and_no_crash(db_session, admin_principal, pending_request):
    """notifications.user_id is NOT NULL; a guest requester has no in-app
    channel and decide() must skip rather than fail."""
    pending_request.requester_user_id = None
    db_session.flush()
    _admin, principal = admin_principal

    result = approvals_service.decide(db_session, principal, pending_request.id, approve=True, note="")
    assert result.status is ApprovalStatus.EXECUTED


def test_execution_is_recorded_in_an_executor_span(db_session, admin_principal, pending_request, cleanup_run):
    """Spec 9.2 requires the dispatch to happen inside an `executor` span.
    Nothing else in this suite would notice if the span were dropped, because
    executor tests call the untraced `execute` directly."""
    from app.db.models import Run, RunTrigger, Span, SpanKind

    _admin, principal = admin_principal
    approvals_service.decide(db_session, principal, pending_request.id, approve=True, note="")

    run = db_session.query(Run).filter(
        Run.trigger == RunTrigger.APPROVAL_EXECUTION,
        Run.conversation_id == pending_request.conversation_id,
    ).one()
    try:
        spans = db_session.query(Span).filter(
            Span.run_id == run.id, Span.kind == SpanKind.EXECUTOR,
        ).all()
        assert len(spans) == 1
        assert spans[0].name == "approval.execute"
    finally:
        cleanup_run(run.id)


def test_list_for_admin_filters_by_status(db_session, admin_principal, pending_request):
    pending = approvals_service.list_for_admin(db_session, status=ApprovalStatus.PENDING)
    assert pending_request.id in {r.id for r in pending}
    denied = approvals_service.list_for_admin(db_session, status=ApprovalStatus.DENIED)
    assert pending_request.id not in {r.id for r in denied}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_approvals_service.py -v`
Expected: FAIL — `AttributeError: module 'app.approvals.service' has no attribute 'decide'`.

- [ ] **Step 3: Implement `decide`, `get`, `list_for_admin`**

Append to `backend/app/approvals/service.py` (and update the `create()` docstring, which currently says "executor.py does not exist yet" — it does now):

```python
class NotPending(RuntimeError):
    """A decision was attempted on a request that is not `pending`. This is
    the idempotency guard: without it, a re-submitted approval would execute
    the action a second time."""


def get(db: Session, request_id: uuid.UUID) -> ApprovalRequest | None:
    return db.query(ApprovalRequest).filter(ApprovalRequest.id == request_id).one_or_none()


def list_for_admin(db: Session, *, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
    query = db.query(ApprovalRequest)
    if status is not None:
        query = query.filter(ApprovalRequest.status == status)
    return query.order_by(ApprovalRequest.created_at.desc()).all()


def decide(
    db: Session,
    principal: Principal,
    request_id: uuid.UUID,
    *,
    approve: bool,
    note: str = "",
) -> ApprovalRequest:
    """Records the admin's decision and, on approval, executes the action
    synchronously (spec 9.2).

    Synchronous on purpose: the admin gets the true terminal status in one
    round trip, the executor span nests under a single run, and no test
    needs to poll. The cost is up to ~10s on a send_email approval, bounded
    by the SMTP timeout -- acceptable for a single deliberate admin action,
    and it does not stall the event loop because the calling endpoint is a
    sync `def` that Starlette runs in a threadpool.

    Commits once, at the end, so the decision, its audit row, its execution
    side effects, and its notification are one atomic unit. A failed
    execution is still committed: `failed` with a recorded reason is the
    correct outcome, not a reason to forget the decision happened.
    """
    from datetime import datetime, timezone

    from app.approvals import executor
    from app.audit import record_audit
    from app.db.models import ActorType, NotificationType, RunStatus, RunTrigger
    from app.notifications import service as notifications
    from app.tracing import spans

    request = get(db, request_id)
    if request is None:
        raise LookupError(f"no approval request with id {request_id}")
    if request.status is not ApprovalStatus.PENDING:
        raise NotPending(
            f"approval {request.id} is already {request.status.value!r}; only a pending request can be decided"
        )

    request.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
    request.decided_by_user_id = uuid.UUID(principal.user_id) if principal.user_id else None
    request.decided_at = datetime.now(timezone.utc)
    request.decision_note = note
    db.flush()

    record_audit(
        db,
        actor_type=ActorType.USER,
        actor_id=principal.user_id,
        action="approval.decide",
        target_type="approval_request",
        target_id=str(request.id),
        payload={"approve": approve, "note": note, "action_type": request.action_type.value},
    )

    if approve:
        # No Phase 6 table has a foreign key to `runs`, so starting a run on
        # tracing's own committed connection cannot deadlock against this
        # session's open transaction. Do not add such an FK later without
        # revisiting this.
        handle = spans.start_run(
            RunTrigger.APPROVAL_EXECUTION,
            conversation_id=request.conversation_id,
            user_id=request.requester_user_id,
        )
        try:
            outcome = executor.execute_traced(db, request)
        finally:
            spans.end_run(handle, status=RunStatus.OK if True else RunStatus.ERROR)

        request.status = ApprovalStatus.EXECUTED if outcome.ok else ApprovalStatus.FAILED
        request.executed_at = datetime.now(timezone.utc)
        request.execution_result = outcome.result
        db.flush()

    verb = {
        ApprovalStatus.DENIED: "denied",
        ApprovalStatus.EXECUTED: "approved and executed",
        ApprovalStatus.FAILED: "approved, but execution failed",
    }[request.status]
    notifications.notify(
        db,
        user_id=request.requester_user_id,
        type=NotificationType.APPROVAL_DECIDED,
        title=f"Request REQ-{request.request_number:06d} was {verb}",
        body=note or request.agent_summary,
        link_type="approval_request",
        link_id=request.id,
    )

    db.commit()
    db.refresh(request)
    return request
```

Add the missing imports at the top of the file: `from app.rbac.policy import Principal`.

- [ ] **Step 4: Fix the run status expression**

`RunStatus.OK if True else RunStatus.ERROR` is a placeholder that always yields OK. Replace the `try/finally` with:

```python
        outcome = None
        try:
            outcome = executor.execute_traced(db, request)
        finally:
            spans.end_run(
                handle,
                status=RunStatus.OK if (outcome and outcome.ok) else RunStatus.ERROR,
                error=None if (outcome and outcome.ok) else str((outcome.result if outcome else "executor raised")),
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_approvals_service.py -v`
Expected: all pass.

The span test uses the `cleanup_run` fixture because `app.tracing.store` commits on its own connection, so run and span rows are NOT rolled back by `db_session` at teardown. Skipping it leaks a run per test run.

If a test hangs for more than ~30 seconds, you have hit the `start_run()` / `db_session` deadlock. Diagnose with:

```bash
docker exec postgres18 psql -U postgres -d ticketing -c "select pid, state, wait_event_type, now()-xact_start from pg_stat_activity where datname='ticketing'"
```

Look for `idle in transaction` alongside an `active`/`Lock` row, and recover with `pg_terminate_backend(pid)` on both.

- [ ] **Step 6: Commit**

```bash
git add backend/app/approvals/service.py backend/tests/test_approvals_service.py
git commit -m "Add decide(): the approval state machine and its execution"
```

---

### Task 7: The admin approvals router

**Files:**
- Create: `backend/app/admin/__init__.py` (empty)
- Create: `backend/app/admin/router.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_admin_approvals_router.py`

**Interfaces:**
- Consumes: `approvals.service.{decide, get, list_for_admin, NotPending}` (Task 7), `deps.require_role`.
- Produces: `GET /api/admin/approvals`, `POST /api/admin/approvals/{id}/decide`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_admin_approvals_router.py`. Read `backend/tests/test_tickets_router.py` first and copy its exact authentication helper — do not invent a new one.

```python
from __future__ import annotations

import uuid

import pytest

from app.db.models import (
    ApprovalActionType, ApprovalRequest, ApprovalStatus, Conversation, RiskLevel, Role, User,
)


@pytest.fixture()
def pending(db_session):
    conv = Conversation(guest_name="G", guest_email="g@northstar.example")
    db_session.add(conv)
    db_session.flush()
    row = ApprovalRequest(
        conversation_id=conv.id, task_id=None, requester_user_id=None,
        action_type=ApprovalActionType.RESET_CREDENTIAL,
        action_payload={"target_username": "someone", "credential_kind": "password"},
        justification="j", risk_level=RiskLevel.HIGH, agent_summary="a",
        status=ApprovalStatus.PENDING,
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_listing_requires_admin(client, auth_headers_for_role):
    assert client.get("/api/admin/approvals", headers=auth_headers_for_role("employee")).status_code == 403
    assert client.get("/api/admin/approvals", headers=auth_headers_for_role("helpdesk")).status_code == 403
    assert client.get("/api/admin/approvals", headers=auth_headers_for_role("admin")).status_code == 200


def test_listing_returns_the_agent_justification_and_full_payload(client, auth_headers_for_role, pending):
    """Spec 15's Approvals screen shows the justification, risk level, and
    full payload -- the admin cannot judge a request they cannot see."""
    body = client.get("/api/admin/approvals?status=pending", headers=auth_headers_for_role("admin")).json()
    entry = next(e for e in body if e["id"] == str(pending.id))
    assert entry["justification"] == "j"
    assert entry["risk_level"] == "high"
    assert entry["action_payload"] == {"target_username": "someone", "credential_kind": "password"}
    assert entry["request_number"] == f"REQ-{pending.request_number:06d}"


def test_deciding_requires_admin(client, auth_headers_for_role, pending):
    response = client.post(
        f"/api/admin/approvals/{pending.id}/decide",
        json={"approve": True, "note": ""},
        headers=auth_headers_for_role("employee"),
    )
    assert response.status_code == 403


def test_approving_returns_the_terminal_status_in_one_round_trip(client, auth_headers_for_role, pending):
    response = client.post(
        f"/api/admin/approvals/{pending.id}/decide",
        json={"approve": True, "note": "Looks fine"},
        headers=auth_headers_for_role("admin"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["execution_result"]["simulated"] is True


def test_deciding_twice_is_a_conflict(client, auth_headers_for_role, pending):
    headers = auth_headers_for_role("admin")
    client.post(f"/api/admin/approvals/{pending.id}/decide", json={"approve": True, "note": ""}, headers=headers)
    second = client.post(
        f"/api/admin/approvals/{pending.id}/decide", json={"approve": True, "note": ""}, headers=headers,
    )
    assert second.status_code == 409


def test_deciding_an_unknown_request_is_404(client, auth_headers_for_role):
    response = client.post(
        f"/api/admin/approvals/{uuid.uuid4()}/decide",
        json={"approve": True, "note": ""},
        headers=auth_headers_for_role("admin"),
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Add the `auth_headers_for_role` fixture if it does not exist**

Run: `grep -rn "auth_headers" backend/tests/conftest.py backend/tests/test_tickets_router.py | head`

If no shared fixture exists, add one to `backend/tests/conftest.py` modelled exactly on however `test_tickets_router.py` builds its authenticated requests. Do not invent a second auth mechanism.

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_admin_approvals_router.py -v`
Expected: FAIL — 404 on every route (the router does not exist).

- [ ] **Step 4: Implement the router**

Create `backend/app/admin/__init__.py` (empty) and `backend/app/admin/router.py`:

```python
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
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.approvals import service as approvals
from app.db.models import ApprovalStatus
from app.deps import DbSession, require_role
from app.rbac.policy import Principal

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
    try:
        decided = approvals.decide(db, principal, request_id, approve=payload.approve, note=payload.note)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such approval request")
    except approvals.NotPending as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return _serialize(decided)
```

- [ ] **Step 5: Register the router**

In `backend/app/main.py`, add the import alongside the others and register it:

```python
from app.admin.router import router as admin_router
...
app.include_router(admin_router)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_admin_approvals_router.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/admin backend/app/main.py backend/tests/test_admin_approvals_router.py backend/tests/conftest.py
git commit -m "Add the admin approvals queue and decide endpoint"
```

---

### Task 8: The notifications router and SSE stream

**Files:**
- Create: `backend/app/notifications/router.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_notifications_router.py`

**Interfaces:**
- Consumes: `broker.subscribe` (Task 2), `service.{list_for_user, mark_read}` (Task 3).
- Produces: `GET /api/notifications`, `GET /api/notifications/stream`, `POST /api/notifications/{id}/read`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_notifications_router.py`:

```python
from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

from app.db.models import NotificationType
from app.notifications import broker, service


def test_listing_returns_this_users_notifications_only(client, auth_headers_for_role, db_session, current_user_for_role):
    mine = current_user_for_role("employee")
    service.notify(db_session, user_id=mine.id, type=NotificationType.TICKET_CREATED, title="Mine", body="b")
    service.notify(db_session, user_id=uuid.uuid4(), type=NotificationType.TICKET_CREATED, title="Theirs", body="b")
    db_session.commit()

    body = client.get("/api/notifications", headers=auth_headers_for_role("employee")).json()
    titles = [n["title"] for n in body]
    assert "Mine" in titles
    assert "Theirs" not in titles


def test_marking_read_sets_read_at(client, auth_headers_for_role, db_session, current_user_for_role):
    mine = current_user_for_role("employee")
    row = service.notify(db_session, user_id=mine.id, type=NotificationType.TICKET_RESOLVED, title="T", body="b")
    db_session.commit()

    headers = auth_headers_for_role("employee")
    assert client.post(f"/api/notifications/{row.id}/read", headers=headers).status_code == 200
    remaining = client.get("/api/notifications?unread_only=true", headers=headers).json()
    assert [n["id"] for n in remaining] == []


def test_marking_someone_elses_notification_is_404(client, auth_headers_for_role, db_session):
    row = service.notify(db_session, user_id=uuid.uuid4(), type=NotificationType.TICKET_RESOLVED, title="T", body="b")
    db_session.commit()
    response = client.post(f"/api/notifications/{row.id}/read", headers=auth_headers_for_role("employee"))
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_the_stream_replays_unread_rows_then_delivers_live_events(
    db_session, auth_headers_for_role, current_user_for_role,
):
    """TestClient cannot do this: its synchronous iteration cannot interleave
    a publish with a read. ASGITransport keeps a real event loop running."""
    from app.main import app

    mine = current_user_for_role("employee")
    service.notify(db_session, user_id=mine.id, type=NotificationType.TICKET_CREATED, title="Backlog", body="b")
    db_session.commit()

    headers = auth_headers_for_role("employee")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream("GET", "/api/notifications/stream", headers=headers) as response:
            assert response.status_code == 200
            lines = response.aiter_lines()

            replayed = await asyncio.wait_for(_next_event(lines), timeout=5)
            assert replayed["title"] == "Backlog"

            broker.publish(mine.id, {"type": "ticket_assigned", "id": str(uuid.uuid4()), "title": "Live", "body": "b"})
            live = await asyncio.wait_for(_next_event(lines), timeout=5)
            assert live["title"] == "Live"


async def _next_event(lines) -> dict:
    """Skips SSE keepalive comments (lines beginning with ':') and blanks."""
    async for line in lines:
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError("stream ended before an event arrived")
```

- [ ] **Step 2: Add the `current_user_for_role` fixture**

In `backend/tests/conftest.py`, add a fixture returning the `User` row that `auth_headers_for_role` authenticates as, so a test can address notifications to that same user. Build it from whatever `auth_headers_for_role` already does — one source of truth, not two.

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_notifications_router.py -v`
Expected: FAIL — 404 on every route.

- [ ] **Step 4: Implement the router**

Create `backend/app/notifications/router.py`:

```python
"""The per-user notification feed and its SSE channel (spec 10).

The stream SUBSCRIBES BEFORE IT REPLAYS. Replaying first and subscribing
afterwards would silently drop anything published in the gap between the
two, which is exactly the window a busy admin approving a queue would hit.
Events are deduplicated by id so a row that is both replayed and published
arrives once.

A keepalive comment every 15 seconds stops proxies closing an idle stream.
"""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.deps import CurrentPrincipal, DbSession
from app.notifications import broker
from app.notifications import service

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

_KEEPALIVE_SECONDS = 15


class NotificationResponse(BaseModel):
    id: str
    type: str
    title: str
    body: str
    link_type: str | None
    link_id: str | None
    read: bool


def _serialize(row) -> NotificationResponse:
    return NotificationResponse(
        id=str(row.id), type=row.type.value, title=row.title, body=row.body,
        link_type=row.link_type, link_id=str(row.link_id) if row.link_id else None,
        read=row.read_at is not None,
    )


def _require_user(principal) -> uuid.UUID:
    """Guests have no notifications: notifications.user_id is NOT NULL and a
    guest is not a row in `users` (spec 5.1)."""
    if principal.kind != "user" or not principal.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "guests have no notification feed")
    return uuid.UUID(principal.user_id)


@router.get("", response_model=list[NotificationResponse])
def list_notifications(
    principal: CurrentPrincipal, db: DbSession, unread_only: bool = False,
) -> list[NotificationResponse]:
    user_id = _require_user(principal)
    return [_serialize(r) for r in service.list_for_user(db, user_id, unread_only=unread_only)]


@router.post("/{notification_id}/read", response_model=NotificationResponse)
def mark_notification_read(
    notification_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession,
) -> NotificationResponse:
    user_id = _require_user(principal)
    row = service.mark_read(db, user_id, notification_id)
    if row is None:
        # Same 404 whether the row is missing or belongs to someone else, so
        # the endpoint does not leak which ids exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such notification")
    db.commit()
    return _serialize(row)


@router.get("/stream")
async def stream_notifications(principal: CurrentPrincipal, db: DbSession) -> StreamingResponse:
    user_id = _require_user(principal)
    unread = [
        {
            "type": r.type.value, "id": str(r.id), "title": r.title, "body": r.body,
            "link_type": r.link_type, "link_id": str(r.link_id) if r.link_id else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in service.list_for_user(db, user_id, unread_only=True)
    ]

    async def event_stream():
        seen: set[str] = set()
        with broker.subscribe(user_id) as subscription:
            for event in unread:
                seen.add(event["id"])
                yield f"data: {json.dumps(event)}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(subscription.get(), timeout=_KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                except broker.SubscriberDropped:
                    # This client fell too far behind and the broker stopped
                    # queueing for it. Close the stream rather than pretend it is
                    # still live: the browser reconnects and the replay above
                    # re-delivers everything it missed from the database, so
                    # nothing durable is lost.
                    return
                if event.get("id") in seen:
                    continue
                seen.add(event.get("id"))
                yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

Note the subscribe-before-replay ordering: `unread` is *read* before the generator starts, but it is *emitted* inside the `with broker.subscribe(...)` block, so no publish can slip between the subscription opening and the backlog being sent.

- [ ] **Step 5: Register the router**

In `backend/app/main.py`:

```python
from app.notifications.router import router as notifications_router
...
app.include_router(notifications_router)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_notifications_router.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/notifications/router.py backend/app/main.py backend/tests/test_notifications_router.py backend/tests/conftest.py
git commit -m "Add the notification feed and its SSE stream"
```

---

### Task 9: Retrofit the four ticket notification triggers

**Files:**
- Modify: `backend/app/tickets/service.py`
- Create: `backend/tests/test_tickets_notifications.py`

**Interfaces:**
- Consumes: `notifications.service.notify` (Task 3).
- Produces: no new public functions; existing signatures unchanged.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_tickets_notifications.py`:

```python
from __future__ import annotations

import uuid

import pytest

from app.db.models import (
    Notification, NotificationType, Role, TicketStatus, User,
)
from app.tickets import service as tickets


@pytest.fixture()
def helpdesk_user(db_session):
    row = User(
        username=f"hd{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@northstar.example",
        full_name="Test User", password_hash="x", role=Role.HELPDESK, helpdesk_ref="HD-905", is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _notifications_of(db_session, user_id, type_):
    return db_session.query(Notification).filter(
        Notification.user_id == user_id, Notification.type == type_,
    ).all()


def test_reassign_notifies_the_new_assignee(db_session, make_ticket, helpdesk_user):
    ticket = make_ticket(assignee_helpdesk_ref="HD-901")
    tickets.reassign(db_session, ticket, assignee_helpdesk_ref="HD-905", rationale="Specialist match")
    db_session.flush()
    assert len(_notifications_of(db_session, helpdesk_user.id, NotificationType.TICKET_ASSIGNED)) == 1


def test_an_approved_cross_department_assignment_notifies_exactly_once(
    db_session, make_ticket, helpdesk_user,
):
    """reassign() is the single owner of the TICKET_ASSIGNED trigger. The
    executor handler calls reassign() and must NOT emit its own notification
    -- doing so would send the assignee two identical ones, which no
    single-module test would catch."""
    import uuid as _uuid

    from app.approvals import executor
    from app.db.models import (
        ApprovalActionType, ApprovalRequest, ApprovalStatus, RiskLevel,
    )

    ticket = make_ticket(assignee_helpdesk_ref="HD-901")
    approval = ApprovalRequest(
        conversation_id=ticket.conversation_id, task_id=None, requester_user_id=None,
        action_type=ApprovalActionType.CROSS_DEPARTMENT_TICKET_ASSIGNMENT,
        action_payload={
            "ticket_id": str(ticket.id), "assignee_helpdesk_ref": "HD-905",
            "rationale": "Specialist match",
        },
        justification="j", risk_level=RiskLevel.LOW, agent_summary="a",
        status=ApprovalStatus.APPROVED,
    )
    db_session.add(approval)
    db_session.flush()

    outcome = executor.execute(db_session, approval)
    db_session.flush()
    assert outcome.ok is True
    assert len(_notifications_of(db_session, helpdesk_user.id, NotificationType.TICKET_ASSIGNED)) == 1


def test_status_change_notifies_the_requester(db_session, make_ticket):
    requester = User(
        username=f"u{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@northstar.example",
        full_name="Test User", password_hash="x", role=Role.EMPLOYEE, is_active=True,
    )
    db_session.add(requester)
    db_session.flush()

    ticket = make_ticket(requester_user_id=requester.id)
    tickets.transition_status(db_session, ticket, TicketStatus.IN_PROGRESS)
    db_session.flush()
    assert len(_notifications_of(db_session, requester.id, NotificationType.TICKET_STATUS_CHANGED)) == 1


def test_resolve_notifies_the_requester(db_session, make_ticket):
    requester = User(
        username=f"u{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@northstar.example",
        full_name="Test User", password_hash="x", role=Role.EMPLOYEE, is_active=True,
    )
    db_session.add(requester)
    db_session.flush()

    ticket = make_ticket(requester_user_id=requester.id, status=TicketStatus.IN_PROGRESS)
    tickets.resolve_ticket(db_session, ticket, resolution="Rotated the cert.", resolved_by_user_id=None)
    db_session.flush()
    assert len(_notifications_of(db_session, requester.id, NotificationType.TICKET_RESOLVED)) == 1


def test_a_guest_requester_produces_no_notification_and_no_crash(db_session, make_ticket):
    """make_ticket defaults to a guest requester. notifications.user_id is
    NOT NULL, so the retrofit must skip rather than fail."""
    ticket = make_ticket()
    tickets.transition_status(db_session, ticket, TicketStatus.IN_PROGRESS)
    db_session.flush()  # must not raise


def test_resolve_does_not_double_notify(db_session, make_ticket):
    """resolve_ticket calls transition_status internally. The requester must
    get one TICKET_RESOLVED notification, not a status-changed one as well."""
    requester = User(
        username=f"u{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@northstar.example",
        full_name="Test User", password_hash="x", role=Role.EMPLOYEE, is_active=True,
    )
    db_session.add(requester)
    db_session.flush()

    ticket = make_ticket(requester_user_id=requester.id, status=TicketStatus.IN_PROGRESS)
    tickets.resolve_ticket(db_session, ticket, resolution="Done.", resolved_by_user_id=None)
    db_session.flush()

    assert len(_notifications_of(db_session, requester.id, NotificationType.TICKET_RESOLVED)) == 1
    assert len(_notifications_of(db_session, requester.id, NotificationType.TICKET_STATUS_CHANGED)) == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tickets_notifications.py -v`
Expected: FAIL — assertion errors, `0 != 1`.

- [ ] **Step 3: Add the notification emissions**

In `backend/app/tickets/service.py`, import inside each function (module-scope would create a cycle: `notifications.service` does not import tickets, but `executor` imports both):

In `create_ticket`, immediately before its `db.commit()`:

```python
    # Spec 10's "ticket created for you" / "ticket assigned to you". Both are
    # skipped for a guest requester -- notifications.user_id is NOT NULL and
    # guests are not rows in `users` (spec 5.1); notify() returns None rather
    # than raising, so no guard is needed here.
    from app.db.models import NotificationType
    from app.notifications import service as notifications

    notifications.notify(
        db, user_id=requester_user_id, type=NotificationType.TICKET_CREATED,
        title=f"Ticket TCK-{ticket.ticket_number:06d} created",
        body=title, link_type="ticket", link_id=ticket.id,
    )
    notifications.notify(
        db, user_id=ticket.assignee_user_id, type=NotificationType.TICKET_ASSIGNED,
        title=f"Ticket TCK-{ticket.ticket_number:06d} assigned to you",
        body=assignment_rationale, link_type="ticket", link_id=ticket.id,
    )
```

In `transition_status`, after the status is set and before `return ticket`:

```python
    from app.db.models import NotificationType, TicketStatus as _TS
    from app.notifications import service as notifications

    # resolve_ticket calls this function and then sends its own, more
    # specific TICKET_RESOLVED notification. Emitting a status-changed one
    # here as well would give the requester two notifications for one event.
    if target is not _TS.RESOLVED:
        notifications.notify(
            db, user_id=ticket.requester_user_id, type=NotificationType.TICKET_STATUS_CHANGED,
            title=f"Ticket TCK-{ticket.ticket_number:06d} is now {target.value}",
            body=ticket.title, link_type="ticket", link_id=ticket.id,
        )
```

In `reassign`, after the assignee columns are set and before `return ticket`:

```python
    from app.db.models import NotificationType
    from app.notifications import service as notifications

    notifications.notify(
        db, user_id=ticket.assignee_user_id, type=NotificationType.TICKET_ASSIGNED,
        title=f"Ticket TCK-{ticket.ticket_number:06d} assigned to you",
        body=rationale, link_type="ticket", link_id=ticket.id,
    )
```

In `resolve_ticket`, before `return ticket`:

```python
    from app.db.models import NotificationType
    from app.notifications import service as notifications

    notifications.notify(
        db, user_id=ticket.requester_user_id, type=NotificationType.TICKET_RESOLVED,
        title=f"Ticket TCK-{ticket.ticket_number:06d} resolved",
        body=ticket.resolution, link_type="ticket", link_id=ticket.id,
    )
```

- [ ] **Step 4: Wire `attachment_requested`**

In `backend/app/agent/tools/approvals_and_attachments.py`, `request_attachment_handler` currently touches no database. Give it the notification, keeping its existing return value unchanged:

```python
async def request_attachment_handler(
    principal: Principal, db: Session, args: RequestAttachmentArgs, *, conversation_id: uuid.UUID | None = None,
) -> dict:
    """Emits a structured signal for the SSE layer to turn into an
    `attachment_request` event, and -- for a signed-in user -- a durable
    notification so the request survives the user closing the tab. Still
    does not block the turn (spec 8.3)."""
    from app.db.models import NotificationType
    from app.notifications import service as notifications

    notifications.notify(
        db,
        user_id=uuid.UUID(principal.user_id) if principal.kind == "user" and principal.user_id else None,
        type=NotificationType.ATTACHMENT_REQUESTED,
        title=f"The assistant asked for a {args.kind}",
        body=args.reason,
        link_type="conversation",
        link_id=conversation_id,
    )
    return {"attachment_requested": True, "kind": args.kind, "reason": args.reason}
```

Check `backend/app/agent/registry.py` for how this handler is invoked. If it is not currently passed `conversation_id`, follow the keyword-only `extra_context` mechanism that `create_ticket_handler` already uses — do not let the model supply it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tickets_notifications.py tests/test_agent_tools_approvals_and_attachments.py -v`
Expected: all pass. Update the existing attachment tool test if its call signature changed.

- [ ] **Step 6: Run the full tickets suite for regressions**

Run: `uv run pytest tests/test_tickets_service.py tests/test_tickets_lifecycle.py tests/test_tickets_router.py tests/test_tickets_router_patch.py tests/test_tickets_router_resolve.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/tickets/service.py backend/app/agent/tools/approvals_and_attachments.py backend/tests/test_tickets_notifications.py backend/tests/test_agent_tools_approvals_and_attachments.py
git commit -m "Emit notifications on every ticket lifecycle event"
```

---

### Task 10: The Phase 6 gate test

**Files:**
- Create: `backend/tests/test_phase6_gate.py`

**Interfaces:**
- Consumes: everything above. Produces nothing.

- [ ] **Step 1: Write the gate test**

Create `backend/tests/test_phase6_gate.py`:

```python
"""Spec 18, phase 6 gate: 'Approve -> execute -> SSE + email; the "no email
without approval" invariant test passes.'

This is one continuous path, deliberately not decomposed: each half passing
in isolation is what the per-module tests already prove. What this test adds
is that an admin's single HTTP approval causes a real email row to reach
`sent` AND a live SSE subscriber to receive the decision -- in one flow, in
the right order.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

from app.db.models import (
    ApprovalActionType, ApprovalRequest, ApprovalStatus, Conversation, EmailStatus,
    OutboundEmail, RiskLevel,
)
from app.notifications import email as email_module


class _AcceptingTransport:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, message, *, to_address: str) -> str:
        self.sent.append(to_address)
        return "250 2.0.0 OK"


@pytest.mark.asyncio
async def test_approve_then_execute_then_sse_and_email(
    db_session, auth_headers_for_role, current_user_for_role, monkeypatch,
):
    from app.main import app
    from app.db.session import get_db as _get_db

    transport_recorder = _AcceptingTransport()
    monkeypatch.setattr(email_module, "_transport", transport_recorder)
    monkeypatch.setattr(email_module, "allowlist_patterns", lambda: ["*@northstar.example"])

    requester = current_user_for_role("employee")
    conv = Conversation(user_id=requester.id, title="VPN help")
    db_session.add(conv)
    db_session.flush()

    request = ApprovalRequest(
        conversation_id=conv.id, task_id=None, requester_user_id=requester.id,
        action_type=ApprovalActionType.SEND_EMAIL,
        action_payload={
            "to_address": "ops@northstar.example",
            "subject": "VPN certificate rotation",
            "body": "Please rotate the VPN certificate for this user.",
        },
        justification="The user cannot connect and the certificate has expired.",
        risk_level=RiskLevel.MEDIUM, agent_summary="Email ops to rotate a VPN certificate.",
        status=ApprovalStatus.PENDING,
    )
    db_session.add(request)
    db_session.commit()

    def _override_get_db():
        yield db_session

    app.dependency_overrides[_get_db] = _override_get_db
    try:
        asgi = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=asgi, base_url="http://test") as ac:
            # The requester is listening on their notification stream before
            # the admin acts -- this is the "SSE" half of the gate.
            async with ac.stream(
                "GET", "/api/notifications/stream", headers=auth_headers_for_role("employee"),
            ) as stream:
                assert stream.status_code == 200
                lines = stream.aiter_lines()

                response = await ac.post(
                    f"/api/admin/approvals/{request.id}/decide",
                    json={"approve": True, "note": "Approved -- expired cert confirmed."},
                    headers=auth_headers_for_role("admin"),
                )
                assert response.status_code == 200, response.text
                body = response.json()

                # Approve -> execute
                assert body["status"] == "executed"
                assert body["execution_result"]["email_status"] == "sent"

                # -> SSE
                event = await asyncio.wait_for(_next_event(lines), timeout=10)
                assert event["type"] == "approval_decided"
                assert "approved and executed" in event["title"]
    finally:
        app.dependency_overrides.clear()

    # -> email
    assert transport_recorder.sent == ["ops@northstar.example"]
    row = db_session.query(OutboundEmail).filter(
        OutboundEmail.approval_request_id == request.id,
    ).one()
    assert row.status is EmailStatus.SENT
    assert row.sent_at is not None
    assert row.approval_status_at_send in {ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED}


async def _next_event(lines) -> dict:
    async for line in lines:
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError("stream ended before the decision event arrived")
```

- [ ] **Step 2: Run the gate test**

Run: `uv run pytest tests/test_phase6_gate.py -v`
Expected: PASS.

If the SSE event never arrives, the most likely cause is that `POST /decide` is `async def` — a blocking commit inside an async endpoint prevents the stream's generator from being scheduled. Confirm it is a plain `def`.

- [ ] **Step 3: Run it three times consecutively**

Run: `uv run pytest tests/test_phase6_gate.py -v --count=3` (or three separate invocations if `pytest-repeat` is not installed).
Expected: 3/3 pass. A gate that passes once is not evidence.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_phase6_gate.py
git commit -m "Add the phase 6 gate test: approve, execute, SSE, email"
```

---

### Task 11: The live SMTP test, docs, and the full suite

**Files:**
- Create: `backend/tests/test_email_live_smtp.py`
- Modify: `README.md`
- Modify: `.env` (local only, not committed)

**Interfaces:** none.

- [ ] **Step 1: Check how live tests are marked**

Run: `grep -n "live" backend/pyproject.toml backend/tests/test_agent_live_api.py | head -20`

Use that exact marker and skip condition. Do not invent a second convention.

- [ ] **Step 2: Write the live test**

Create `backend/tests/test_email_live_smtp.py`, substituting the real marker found in Step 1:

```python
"""Sends ONE genuine email through the configured SMTP server. Excluded from
the default run like every other live test -- `uv run python tasks.py test`
costs nothing and sends nothing.

This exists because every other email test replaces the transport, so none
of them prove the real socket, the real TLS mode, or the real credentials
work. Phase 5 taught the lesson twice: the live run is what finds the bug
nothing offline catches.
"""
from __future__ import annotations

import uuid

import pytest

from app.config import get_settings
from app.db.models import (
    ApprovalActionType, ApprovalRequest, ApprovalStatus, Conversation, EmailStatus, RiskLevel,
)
from app.notifications import email as email_module

pytestmark = pytest.mark.live


@pytest.fixture()
def live_recipient():
    patterns = email_module.allowlist_patterns()
    concrete = [p for p in patterns if "*" not in p]
    if not concrete:
        pytest.skip("EMAIL_RECIPIENT_ALLOWLIST has no concrete address to send to")
    return concrete[0]


def test_a_real_email_is_delivered(db_session, live_recipient):
    settings = get_settings()
    if not settings.smtp_host:
        pytest.skip("SMTP_HOST is not configured")

    conv = Conversation(guest_name="G", guest_email="g@northstar.example")
    db_session.add(conv)
    db_session.flush()
    approval = ApprovalRequest(
        conversation_id=conv.id, task_id=None, requester_user_id=None,
        action_type=ApprovalActionType.SEND_EMAIL,
        action_payload={"to_address": live_recipient, "subject": "s", "body": "b"},
        justification="live smtp verification", risk_level=RiskLevel.LOW,
        agent_summary="a", status=ApprovalStatus.APPROVED,
    )
    db_session.add(approval)
    db_session.flush()

    marker = uuid.uuid4().hex[:8]
    row = email_module.send(
        db_session, approval=approval, to_address=live_recipient,
        subject=f"[ticketing] phase 6 live SMTP check {marker}",
        body=(
            "This message confirms the phase 6 email path: approval -> executor -> "
            f"smtplib -> outbound_emails. Marker {marker}."
        ),
    )

    assert row.status is EmailStatus.SENT, f"send failed: {row.smtp_response}"
    assert row.sent_at is not None
    print(f"\nDelivered to {live_recipient}; marker {marker}; response {row.smtp_response}")
```

- [ ] **Step 3: Configure `.env` for the real send**

Add to `.env` (git-ignored, do not commit):

```
SMTP_SECURE=true
EMAIL_RECIPIENT_ALLOWLIST=alakak1376@gmail.com,*@northstar.example
```

`SMTP_PORT` is already 465, which forces implicit TLS regardless, but set `SMTP_SECURE` explicitly so the intent is legible.

- [ ] **Step 4: Run the live test**

Run: `uv run pytest tests/test_email_live_smtp.py -v -s -m live`
Expected: PASS, and a real email arrives in the inbox. **Confirm the email actually arrived before recording the gate as met** — a `250 OK` means the server accepted the message, not that it was delivered.

If it fails with `SMTPAuthenticationError`, the Gmail account needs an App Password rather than the account password; report that rather than working around it.

- [ ] **Step 5: Document the phase in the README**

Add a short section covering: the two new endpoint groups, `SMTP_SECURE` and `EMAIL_RECIPIENT_ALLOWLIST` (including that empty means nobody), the fact that three action types are simulated and which, and the single-worker limitation of the in-process broker.

- [ ] **Step 6: Run the full suite**

Run: `uv run python tasks.py test`
Expected: **0 failed.** The baseline was 327 passed; this phase adds roughly 60 tests.

Investigate any failure as real. There are no known-flaky tests in this project.

- [ ] **Step 7: Run the full suite a second time**

Run: `uv run python tasks.py test`
Expected: identical result. A suite that passes once is not evidence, and this phase adds concurrency.

- [ ] **Step 8: Check for stray files**

Run: `git status --short`
Expected: clean, or only intended changes. Subagents doing DB-level work sometimes leave scratch scripts behind.

- [ ] **Step 9: Commit**

```bash
git add backend/tests/test_email_live_smtp.py README.md
git commit -m "Add the live SMTP test and document the phase 6 surface"
```

---

## Self-Review Notes

**Spec coverage.** Every section of the phase 6 design maps to a task: §2.1→T5, §2.2→T1, §2.3→T1, §2.4→T4, §3→T2–T8, §4→T6, §5.1→T5, §5.2→T5, §6→T4, §7→T1, §8.1→T2, §8.2→T3, §8.3→T8, §8.4→T9, §8.5→T3+T6+T9, §9→T7+T8, §10→every task plus T10–T11, §11→T6 and T8 notes.

**Deliberately deferred.** `POST /api/notifications/{id}/read` returning the full row rather than 204 is a small choice made for testability. The `disclose_restricted_information` handler posts a system message but does not push it down the *chat* SSE stream — that stream only exists during an active turn, and a conversation-level push channel is not in this phase's scope.

**Known risk.** Task 9 adds `notify()` calls inside `tickets/service.py` functions that Phase 5 tests already exercise heavily. If those tests break, the cause is almost certainly a `User` row that does not exist for a `requester_user_id`, not a bug in the notification code — check the fixture before changing the service.
