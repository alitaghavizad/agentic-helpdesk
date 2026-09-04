# Phase 10 (Hardening, README, Full Test Pass) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (design decision D2: direct execution, not subagent-driven-development — every task here is a diagnosed bug with an already-proven fix pattern in this codebase, not new design). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close out the project: fix six previously-diagnosed backlog defects, document the missing Phase 9 README section, and produce a final report with fresh eval numbers and a live manual walkthrough.

**Architecture:** No new subsystem. Six independent, disjoint-file bug fixes (tasks 1-6), then two documentation tasks (7-8) that depend on everything before them being green.

**Tech Stack:** Same as the rest of the backend — Python/FastAPI/SQLAlchemy/Alembic/pytest, `uv`-managed venv at `backend/.venv`.

## Global Constraints

- Run tests with `cd backend && .venv/Scripts/python.exe -m pytest ...` (not `uv run`, which triggers slow reinstalls the venv doesn't need).
- Postgres (`postgres18`) and Chroma (`chroma`) containers must be running: `docker start postgres18 chroma`, verify with `docker exec postgres18 pg_isready -U postgres` and `curl http://localhost:8000/api/v2/heartbeat`.
- Full backend suite: `cd backend && .venv/Scripts/python.exe tasks.py test` (~5-7 min). Live-marked tests (`live_api`, `live_smtp`, `live_gemini`, `live_dossier`, `live_reflection`) are excluded by `pyproject.toml`'s `addopts` and must stay excluded — do not add `-m` overrides that include them.
- Frontend suite (only touched by task 7/8's verification, not modified by any task): `cd frontend && npm test && npm run typecheck && npm run build`.
- Every test that hard-commits a row via `get_sessionmaker()` (bypassing `db_session`'s rollback) must clean up in a `finally`/fixture-teardown, never after its own assertions — an assertion failure before cleanup leaks the row into the shared dev Postgres permanently (this has already happened once this project and corrupted three unrelated test files' counts).
- Every fix in this plan must be proven by mutation: apply the fix, write the test, then temporarily revert the fix and confirm the test fails with the predicted error before restoring the fix — "the test passes" alone is not sufficient evidence.
- Commit after each task with `git add <exact files>` (never `git add -A`).

---

### Task 1: Stop the `runs` row leak in `test_ingest_dataset.py` / `test_eval_retrieval.py`

**Files:**
- Modify: `backend/tests/test_ingest_dataset.py`
- Modify: `backend/tests/test_eval_retrieval.py`

**Interfaces:**
- Consumes: `app.db.models.Run`, `Run.trigger == RunTrigger.INGEST_EVAL`, `app.db.models.Span`, `app.db.session.get_sessionmaker()` — all pre-existing.
- Produces: nothing consumed by a later task.

**Context:** `scripts/ingest_dataset.py::main()` and `scripts/eval_retrieval.py::run_eval()` each correctly bracket `start_run(RunTrigger.INGEST_EVAL)` / `end_run(...)` (no stuck-`RUNNING` bug) but nothing outside the script ever deletes the finalized `Run` row. `test_ingest_dataset_closes_backend_on_chunking_failure` calls `main()` once; `test_ingest_dataset_populates_both_collections_and_is_idempotent` calls it three times (twice in its body, once in a `finally`); `test_retrieval_recall_at_5_meets_accepted_floor` calls `run_eval()` once or twice depending on its retry loop — 5-6 leaked rows per full-suite run, matching the previously-observed 5-10.

- [ ] **Step 1: Add the cleanup fixture to `test_ingest_dataset.py`**

Add this fixture near the top of `backend/tests/test_ingest_dataset.py`, after the existing imports:

```python
@pytest.fixture(autouse=True)
def _cleanup_ingest_eval_runs():
    """ingest_dataset.main() commits a real Run(trigger=INGEST_EVAL) via
    start_run()/end_run() on every call -- there is no stuck-RUNNING bug
    (the script brackets both correctly), but nothing ever deletes the
    finalized row. This file's tests call main() up to three times each;
    left unswept, this accumulates permanently in the shared dev Postgres
    runs table. Same before/after started_at range pattern used repeatedly
    in the phase 9 learning-loop work for the identical leak class."""
    from app.db.models import Run, RunTrigger, Span
    from app.db.session import get_sessionmaker

    Session = get_sessionmaker()
    with Session() as s:
        before = (
            s.query(Run.started_at).filter(Run.trigger == RunTrigger.INGEST_EVAL)
            .order_by(Run.started_at.desc()).first()
        )

    yield

    with Session() as s:
        query = s.query(Run.id).filter(Run.trigger == RunTrigger.INGEST_EVAL)
        if before is not None:
            query = query.filter(Run.started_at > before[0])
        run_ids = [r[0] for r in query.all()]
        if run_ids:
            s.query(Span).filter(Span.run_id.in_(run_ids)).delete(synchronize_session=False)
            s.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
            s.commit()
```

- [ ] **Step 2: Add the identical fixture to `test_eval_retrieval.py`**

Add the same fixture (byte-identical body) near the top of `backend/tests/test_eval_retrieval.py`, after its existing imports. It needs `import pytest` added to that file's imports if not already present — check the top of the file first; if `pytest` is not imported, add `import pytest` alongside the existing `import asyncio`/`import sys` lines.

- [ ] **Step 3: Verify the leak is gone**

Run (from `backend/`):
```bash
.venv/Scripts/python.exe -c "
from app.db.models import Run, RunTrigger
from app.db.session import get_sessionmaker
Session = get_sessionmaker()
with Session() as s:
    print('before:', s.query(Run).filter(Run.trigger == RunTrigger.INGEST_EVAL).count())
"
.venv/Scripts/python.exe -m pytest tests/test_ingest_dataset.py -q
.venv/Scripts/python.exe -c "
from app.db.models import Run, RunTrigger
from app.db.session import get_sessionmaker
Session = get_sessionmaker()
with Session() as s:
    print('after:', s.query(Run).filter(Run.trigger == RunTrigger.INGEST_EVAL).count())
"
```
Expected: `tests/test_ingest_dataset.py` passes, and the before/after counts are equal. Repeat the same three-command shape for `tests/test_eval_retrieval.py` (this one hits the real Anthropic-free retrieval path against real Chroma and can take 30s-2min; that is expected and unrelated to this fix).

- [ ] **Step 4: Run both files together to confirm no interaction**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ingest_dataset.py tests/test_eval_retrieval.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_ingest_dataset.py tests/test_eval_retrieval.py
git commit -m "Stop the runs-row leak in ingest_dataset and eval_retrieval tests"
```

---

### Task 2: Add an index on `lessons.created_at`

**Files:**
- Modify: `backend/app/db/models.py` (the `Lesson` class)
- Create: `backend/alembic/versions/c3f6a1d8e2b7_lessons_created_at_index.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: nothing consumed by a later task.

**Context:** `GET /api/admin/lessons` → `app/admin/queries.py::list_lessons` does `ORDER BY lessons.created_at DESC, lessons.id DESC`, but `Lesson.created_at` has no index — unlike the identically-shaped `Conversation.created_at`, which already has one (`index=True`, added by migration `f9824ef578ed` for the equivalent conversations-list endpoint). Current alembic head is `99abd72c629d`.

- [ ] **Step 1: Add `index=True` to the model column**

In `backend/app/db/models.py`, find the `Lesson` class's `created_at` column (currently `created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)`, immediately after `created_by_run_id`) and change it to:

```python
    # Indexed: the admin lessons list's ORDER BY. See migration
    # c3f6a1d8e2b7.
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False, index=True,
    )
```

- [ ] **Step 2: Write the migration**

Create `backend/alembic/versions/c3f6a1d8e2b7_lessons_created_at_index.py`:

```python
"""lessons created_at index

Revision ID: c3f6a1d8e2b7
Revises: 99abd72c629d
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3f6a1d8e2b7'
down_revision: Union[str, Sequence[str], None] = '99abd72c629d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Phase 10 backlog item: GET /api/admin/lessons orders by
    lessons.created_at with no supporting index, unlike the identically-
    shaped conversations list (see f9824ef578ed).
    """
    op.create_index(op.f('ix_lessons_created_at'), 'lessons', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_lessons_created_at'), table_name='lessons')
```

- [ ] **Step 3: Apply the migration**

Run: `.venv/Scripts/python.exe -m alembic upgrade head`
Expected: no errors; `.venv/Scripts/python.exe -m alembic heads` now reports `c3f6a1d8e2b7 (head)`.

- [ ] **Step 4: Verify no drift was introduced**

Run: `.venv/Scripts/python.exe -m alembic check`
Expected: no output about `ix_lessons_created_at` (it should now match between the model and the DB). Some pre-existing drift lines for the four *other* known items (`uq_approval_requests_id_status`, `ix_notifications_user_id_read_at`, `fk_outbound_emails_approval_status`, `ck_outbound_emails_approved_before_send`) are expected to still appear here — Task 4 fixes those, not this task. Confirm the `ix_lessons_created_at` name does NOT appear anywhere in the output.

- [ ] **Step 5: Run the lessons-related admin tests to confirm nothing broke**

Run: `.venv/Scripts/python.exe -m pytest tests/test_admin_queries.py tests/test_admin_mutations.py tests/test_phase8a_gate.py -q`
Expected: all pass (an added index changes no query results, only their plan).

- [ ] **Step 6: Commit**

```bash
git add app/db/models.py alembic/versions/c3f6a1d8e2b7_lessons_created_at_index.py
git commit -m "Add the missing index on lessons.created_at"
```

---

### Task 3: `Subscription._offer` must not raise `RuntimeError` out of `broker.publish`

**Files:**
- Modify: `backend/app/notifications/broker.py`
- Modify: `backend/tests/test_notifications_broker.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: nothing consumed by a later task.

**Context:** `Subscription._offer` (`app/notifications/broker.py`) calls `self._loop.call_soon_threadsafe(self._put, event)` when the offering thread differs from the subscription's owning loop. If `self._loop` has since closed, `call_soon_threadsafe` raises `RuntimeError: Event loop is closed` synchronously, uncaught — and `publish()`'s `for` loop has no try/except around `subscription._offer(event)`, so it propagates out of `publish()`, which the module's own docstring says is called from SQLAlchemy's synchronous `after_commit` hook. Never reproduced in production; this task adds the missing guard and a test that reproduces it directly. The file already has an established pattern for "this subscriber is unusable": `_put`'s `except asyncio.QueueFull: self.dropped = True`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_notifications_broker.py`:

```python
def test_offer_with_a_closed_loop_drops_the_subscriber_not_raises():
    """A Subscription's captured event loop can close before broker.publish
    is ever called against it (e.g. the SSE connection's own loop shutting
    down before subscribe()'s finally has unregistered it yet). Without a
    guard, call_soon_threadsafe against a closed loop raises RuntimeError,
    which would escape publish() and break delivery to every OTHER
    subscriber on the same call -- publish() is called from SQLAlchemy's
    synchronous after_commit hook per this module's own docstring, so this
    would break a real commit path if it ever fired."""
    user = uuid.uuid4()
    with broker.subscribe(user) as sub:
        closed_loop = asyncio.new_event_loop()
        closed_loop.close()
        sub._loop = closed_loop

        broker.publish(user, {"type": "wont_be_delivered"})

        assert sub.dropped is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_notifications_broker.py::test_offer_with_a_closed_loop_drops_the_subscriber_not_raises -v`
Expected: FAIL with `RuntimeError: Event loop is closed` propagating out of `broker.publish`, not an `AssertionError` — confirming the bug is real before fixing it.

- [ ] **Step 3: Fix `_offer`**

In `backend/app/notifications/broker.py`, change `_offer` from:

```python
    def _offer(self, event: dict) -> None:
        """publish() is called from SQLAlchemy's after_commit, which runs on
        whichever thread committed -- and because this project's endpoints
        are sync `def`, Starlette runs them in a threadpool, NOT on the
        event loop. Touching an asyncio.Queue from another thread races the
        loop's internals, so cross-thread offers are marshalled back onto
        the owning loop."""
        if self.dropped:
            return
        if self._loop is not None:
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is not self._loop:
                self._loop.call_soon_threadsafe(self._put, event)
                return
        self._put(event)
```

to:

```python
    def _offer(self, event: dict) -> None:
        """publish() is called from SQLAlchemy's after_commit, which runs on
        whichever thread committed -- and because this project's endpoints
        are sync `def`, Starlette runs them in a threadpool, NOT on the
        event loop. Touching an asyncio.Queue from another thread races the
        loop's internals, so cross-thread offers are marshalled back onto
        the owning loop.

        The owning loop can have closed by the time this runs (the SSE
        connection's own loop shutting down before subscribe()'s finally
        has unregistered this subscription yet) -- call_soon_threadsafe
        against a closed loop raises RuntimeError synchronously. Caught
        here and treated exactly like _put's QueueFull branch: this
        subscriber is simply unusable now, and that must never break
        delivery to every OTHER subscriber on the same publish() call."""
        if self.dropped:
            return
        if self._loop is not None:
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is not self._loop:
                try:
                    self._loop.call_soon_threadsafe(self._put, event)
                except RuntimeError:
                    self.dropped = True
                return
        self._put(event)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_notifications_broker.py::test_offer_with_a_closed_loop_drops_the_subscriber_not_raises -v`
Expected: PASS.

- [ ] **Step 5: Run the full broker test file**

Run: `.venv/Scripts/python.exe -m pytest tests/test_notifications_broker.py -v`
Expected: all pass, including the pre-existing tests (the fix only adds a `try`/`except` around one call, changing no other behavior).

- [ ] **Step 6: Commit**

```bash
git add app/notifications/broker.py tests/test_notifications_broker.py
git commit -m "Fix Subscription._offer: a closed event loop drops the subscriber, not broker.publish"
```

---

### Task 4: Close the alembic drift from migration `211125c17904`

**Files:**
- Modify: `backend/app/db/models.py` (the `ApprovalRequest`, `OutboundEmail`, and `Notification` classes)

**Interfaces:**
- Consumes: nothing from earlier tasks. Independent of Task 2's model edit (different classes) — either order is fine; if executed after Task 2, note the `Lesson.created_at` edit from Task 2 is already in place and untouched by this task.
- Produces: nothing consumed by a later task.

**Context:** `alembic check` reports the live database already has four objects that `app/db/models.py` does not declare, all added by the hand-written migration `211125c17904`. This task adds matching `__table_args__` declarations — no new migration, since the database is already correct; only the ORM metadata is missing.

- [ ] **Step 1: Add the missing imports**

In `backend/app/db/models.py`, change the `sqlalchemy` import block from:

```python
from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Integer, Numeric,
    String, Text, UniqueConstraint, func,
)
```

to:

```python
from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Identity,
    Index, Integer, Numeric, String, Text, UniqueConstraint, func,
)
```

- [ ] **Step 2: Add the unique constraint to `ApprovalRequest`**

Find the `ApprovalRequest` class (currently no `__table_args__`) and add one as the first line inside the class body, before `id: Mapped[uuid.UUID] = _uuid_pk()`:

```python
class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        # Sole purpose: be a valid target for outbound_emails' composite FK
        # below. id is already the PK, so this is trivially satisfied and
        # costs only the index Postgres builds for it. Added by migration
        # 211125c17904; declared here to close alembic drift.
        UniqueConstraint("id", "status", name="uq_approval_requests_id_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
```

- [ ] **Step 3: Add the composite FK and check constraint to `OutboundEmail`**

Find the `OutboundEmail` class (currently no `__table_args__`) and add one as the first line inside the class body:

```python
class OutboundEmail(Base):
    __tablename__ = "outbound_emails"
    __table_args__ = (
        # Spec 5.3's invariant lives in the database: this FK mirrors the
        # approval's status with ON UPDATE CASCADE, and the CHECK forbids
        # the pre-approval states. Both added by migration 211125c17904;
        # declared here to close alembic drift, not to change behavior --
        # the database has enforced both since that migration ran.
        ForeignKeyConstraint(
            ["approval_request_id", "approval_status_at_send"],
            ["approval_requests.id", "approval_requests.status"],
            name="fk_outbound_emails_approval_status", onupdate="CASCADE",
        ),
        CheckConstraint(
            "approval_status_at_send IN ('approved', 'executed', 'failed')",
            name="ck_outbound_emails_approved_before_send",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
```

- [ ] **Step 4: Add the composite index to `Notification`**

Find the `Notification` class (currently no `__table_args__`) and add one as the first line inside the class body:

```python
class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # Added by migration 211125c17904; declared here to close alembic
        # drift.
        Index("ix_notifications_user_id_read_at", "user_id", "read_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
```

- [ ] **Step 5: Verify the drift is closed**

Run: `.venv/Scripts/python.exe -m alembic check`
Expected: clean exit (no "New upgrade operations detected" error) — all four previously-reported drift items are gone, and Task 2's `ix_lessons_created_at` (if Task 2 ran first) stays clean too, since nothing here touches `Lesson`.

- [ ] **Step 6: Run the affected model's test files**

Run: `.venv/Scripts/python.exe -m pytest tests/test_approvals_service.py tests/test_notifications_broker.py tests/test_notifications_router.py tests/test_admin_mutations.py -q`
Expected: all pass — these declarations describe constraints the database has already been enforcing since `211125c17904`; nothing about actual behavior changes.

- [ ] **Step 7: Commit**

```bash
git add app/db/models.py
git commit -m "Close alembic drift: declare migration 211125c17904's constraints in the ORM models"
```

---

### Task 5: Fix the refresh-token rotation race

**Files:**
- Modify: `backend/app/auth/router.py`
- Modify: `backend/tests/test_auth_router.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: nothing consumed by a later task.

**Context:** `POST /api/auth/refresh` reads the stored `RefreshToken` row, checks `revoked_at is None` in Python, then sets `revoked_at` and commits — the identical read-check-write shape phase 6's approval-decision idempotency bug had (fixed there with `.populate_existing().with_for_update()`). Two concurrent refresh requests presenting the same token can both pass the check and both rotate it. `with_for_update()` alone is a documented trap in this codebase: it takes the lock but the ORM identity map still returns the stale pre-lock instance unless paired with `populate_existing()`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_auth_router.py`. This needs new imports at the top of the file — add `import threading`, `import time`, `import uuid`, `from datetime import datetime, timezone`, and extend the existing bare imports with `from app.auth.router import refresh as refresh_endpoint`, `from app.auth.security import create_refresh_token, hash_password`, `from app.db.models import Clearance, RefreshToken, Role, User`, `from app.db.session import get_sessionmaker`, `from fastapi import HTTPException, Response`:

```python
def test_two_concurrent_refreshes_of_the_same_token_only_one_succeeds():
    """The identical TOCTOU shape phase 6's approval-decision race had:
    refresh() reads revoked_at, checks it in Python, then writes it. This
    forces a genuine overlap the same way test_approvals_service.py's
    concurrent-decisions test does: a raw session takes the row lock and
    holds it open while a second, genuinely concurrent call to the REAL
    refresh() endpoint function is made against the SAME token. With
    with_for_update()+populate_existing(), that second call blocks on the
    lock, then correctly sees the already-revoked row and 401s. Without
    the fix, its plain SELECT would not block, would read the still-
    revoked_at=None row, and would succeed in rotating an already-used
    token."""
    Session = get_sessionmaker()
    with Session() as setup:
        suffix = uuid.uuid4().hex[:8]
        user = User(
            username=f"refr{suffix}", email=f"refr{suffix}@northstar.example",
            full_name="Refresh Race", password_hash=hash_password("Passw0rd!dev"),
            role=Role.EMPLOYEE, clearance=Clearance.STANDARD, is_active=True,
        )
        setup.add(user)
        setup.commit()
        user_id = user.id

        raw_token, token_hash, expires_at = create_refresh_token(subject=str(user_id))
        setup.add(RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at))
        setup.commit()

    lock_held = threading.Event()
    release_lock = threading.Event()

    def _hold_lock():
        with Session() as s:
            stored = (
                s.query(RefreshToken).filter_by(token_hash=token_hash)
                .populate_existing().with_for_update().one()
            )
            lock_held.set()
            release_lock.wait(timeout=15)
            stored.revoked_at = datetime.now(timezone.utc)
            s.commit()

    outcome: dict = {}

    def _try_refresh():
        with Session() as s:
            try:
                refresh_endpoint(response=Response(), db=s, refresh_token=raw_token)
                outcome["result"] = "succeeded"
            except HTTPException as exc:
                outcome["result"] = "rejected"
                outcome["status_code"] = exc.status_code

    try:
        holder = threading.Thread(target=_hold_lock, name="refresh-holder")
        holder.start()
        assert lock_held.wait(timeout=15), "the lock-holding thread never acquired the row lock"

        refresher = threading.Thread(target=_try_refresh, name="refresh-attempt")
        refresher.start()
        time.sleep(0.3)  # give the refresher a real chance to reach and block on the lock
        release_lock.set()
        holder.join(timeout=15)
        refresher.join(timeout=15)
        assert not holder.is_alive() and not refresher.is_alive(), "a thread never finished"

        assert outcome.get("result") == "rejected", (
            f"expected the concurrent refresh to be rejected once the token was "
            f"already revoked, got: {outcome}"
        )
        assert outcome.get("status_code") == 401
    finally:
        with Session() as cleanup:
            cleanup.query(RefreshToken).filter_by(token_hash=token_hash).delete()
            cleanup.query(User).filter_by(id=user_id).delete()
            cleanup.commit()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_auth_router.py::test_two_concurrent_refreshes_of_the_same_token_only_one_succeeds -v`
Expected: FAIL with `outcome.get("result") == "rejected"` false — the current unlocked `refresh()` lets the second call through and succeed, since its plain `SELECT` (no `with_for_update()`) does not block on the holder thread's lock and reads the pre-revocation row.

- [ ] **Step 3: Fix `refresh()`**

In `backend/app/auth/router.py`, change:

```python
    token_hash = hash_refresh_token(refresh_token)
    stored = db.query(RefreshToken).filter_by(token_hash=token_hash).one_or_none()
    if stored is None or stored.revoked_at is not None or stored.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token no longer valid")
```

to:

```python
    token_hash = hash_refresh_token(refresh_token)
    # populate_existing() is required alongside with_for_update(), not
    # decoration: with_for_update() alone takes the row lock but the ORM's
    # identity map still returns whatever instance it already had cached,
    # so a second concurrent caller would wait for the lock and then read
    # the STALE pre-lock revoked_at anyway -- the exact trap phase 6's
    # approval-decision race already paid to learn (see decide() in
    # app/approvals/service.py).
    stored = (
        db.query(RefreshToken).filter_by(token_hash=token_hash)
        .populate_existing().with_for_update().one_or_none()
    )
    if stored is None or stored.revoked_at is not None or stored.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token no longer valid")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_auth_router.py::test_two_concurrent_refreshes_of_the_same_token_only_one_succeeds -v`
Expected: PASS.

- [ ] **Step 5: Run the full auth test file**

Run: `.venv/Scripts/python.exe -m pytest tests/test_auth_router.py -v`
Expected: all pass, including the pre-existing `test_refresh_issues_new_access_token` and `test_logout_revokes_refresh_token` (the fix changes locking, not the single-caller happy path).

- [ ] **Step 6: Commit**

```bash
git add app/auth/router.py tests/test_auth_router.py
git commit -m "Fix the refresh-token rotation race with the same populate_existing()+with_for_update() pattern phase 6 already proved"
```

---

### Task 6: Sweep for other tests defanged by `BaseHTTPMiddleware`

**Files:**
- No files are known in advance — this task is an audit. Any fix it produces modifies whichever test file(s) the sweep finds, following the exact pattern `tests/test_notifications_router.py::test_a_dropped_subscriber_ends_the_generator_instead_of_raising` and `tests/test_admin_runs_stream.py::test_a_dropped_subscriber_ends_the_stream_instead_of_raising` already use (assert on the generator/iterator directly, not over HTTP).

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: nothing consumed by a later task.

**Context:** Phase 8a's final review found that the upload-size-cap middleware (`@app.middleware("http")` in `backend/app/main.py`) puts Starlette's `BaseHTTPMiddleware` in front of every route. That middleware closes the client-facing body the same way whether the downstream handler returned normally or raised, so a test asserting "a handled failure and an unhandled one look different over HTTP" cannot actually distinguish them anymore — already found and fixed once for the notification stream's drop test (both files listed above). This task checks whether the identical blind spot exists anywhere else, since nothing in `backend/app/main.py` scopes that middleware to a subset of routes.

- [ ] **Step 1: Find candidate tests**

Run (from `backend/`):
```bash
grep -rln "escap\|propagat\|SubscriberDropped\|distinguish\|handled.*raise\|raise.*handled" tests/*.py
```
Also read `backend/app/main.py`'s middleware definition (`@app.middleware("http")`, near the top of the file) to confirm exactly what it wraps (currently: every route, enforcing the request body size cap) — this confirms the blind spot applies universally, not to a subset.

For each file the grep returns, read the specific test(s) matched and ask: does this test's stated guarantee depend on distinguishing an exception that escaped a handler from one the handler caught and returned normally, observed **over a real HTTP response** (via a `TestClient` call, not by calling a generator/async-generator directly)? `tests/test_notifications_router.py` and `tests/test_admin_runs_stream.py` will match the grep but are already fixed (confirm their current test asserts on the generator directly, per the already-fixed pattern, and skip them) — the sweep is for any OTHER match.

- [ ] **Step 2: For each remaining candidate, verify it live**

For each candidate test identified in Step 1 that is NOT already fixed: temporarily break the specific guarantee the test's docstring claims (e.g., if a test claims "a handler's raised exception produces a 500," temporarily make that code path swallow the exception and return normally instead — or the reverse, whichever direction the test's docstring claims to guard). Run just that test:

```bash
.venv/Scripts/python.exe -m pytest tests/<candidate_file>.py::<candidate_test> -v
```

If it still passes despite the broken guarantee, the middleware (or something else) is masking the real check — this is a live instance of the same defect class, and needs the same fix `test_notifications_router.py`'s existing fix already demonstrates: rewrite the assertion to check the underlying generator/handler function directly, bypassing the HTTP layer, the same way `test_a_dropped_subscriber_ends_the_generator_instead_of_raising` does. If it correctly fails, the test is fine — revert your temporary break and move to the next candidate.

- [ ] **Step 3: Record the outcome**

If Step 2 found zero additional instances: no code changes are needed for this task. Note this plainly in the final report (Task 8) — "swept for the phase 8a-flagged middleware blind spot across every candidate test found by [grep pattern]; none reproduced" is a real, checked result, not a skipped task.

If Step 2 found one or more additional instances: fix each the same way the two existing fixes do (assert on the generator/handler directly), following Steps 1-5's shape from Task 3 above (write the fix, prove it against the broken-code mutation, restore, run the file). Commit each fix with its own message naming the specific test and guarantee it restores.

- [ ] **Step 4: Commit the audit result**

If no code changed, there is nothing to commit for this task — its outcome is recorded directly in the final report (Task 8). If fixes were made, they were already committed individually in Step 3.

---

### Task 7: Add the missing Phase 9 README section

**Files:**
- Modify: `README.md` (repo root)

**Interfaces:**
- Consumes: nothing from earlier tasks (purely additive documentation).
- Produces: nothing consumed by a later task.

**Context:** The README has a section for every phase (Phase 6, Phase 7, Phase 8a, then Frontend) except Phase 9 (the learning loop). Reflection is background work with no new user-facing endpoint, so the section documents the flow itself rather than an endpoint table, following the existing sections' voice and level of detail.

- [ ] **Step 1: Write the section**

In `README.md`, insert a new section immediately after the existing `## Phase 8a: admin API and the incident dossier` section and before `## Frontend`:

```markdown
## Phase 9: the learning loop

No new endpoint — reflection is background work triggered by an existing
one. When `POST /tickets/{id}/resolve` commits, it schedules
`app.learning.reflect.reflect(ticket_id)` via FastAPI `BackgroundTasks`,
which runs after the HTTP response has already gone out: resolving a
ticket never waits on a model call.

**What happens.** `reflect()` gathers the ticket's own fields, its `Task`,
and the conversation transcript, then asks Claude whether the resolution
taught something worth keeping. The call and its outcome are always traced
as a `Run` (`trigger=reflection`) — whether or not a lesson gets written,
the call was made and billed, and it belongs in cost accounting. If the
model judges the resolution worth recording, a markdown file is written
under `knowledge/lessons/`, a `lessons` row is inserted, and the lesson is
embedded into Chroma's `lessons` collection, making it retrievable through
the agent's existing `search_lessons` tool the next time a similar problem
comes in.

**The `should_record` judgment call is the model's, not a heuristic.** Most
tickets are routine (a password reset, a re-issued badge) and should teach
nothing; recording every resolution would poison retrieval with noise,
worse than recording nothing. `search_lessons_handler`'s own
`where={"status": "active"}` filter also means an admin can archive a bad
lesson (`DELETE /api/admin/lessons/{id}`, phase 8a) to withdraw it from
retrieval without deleting the record that it existed.

**A failed reflection never surfaces anywhere but the log and the run's
own error status.** Nobody is waiting on it — the resolve response already
went out — so a model failure, a schema violation, or an embedding failure
ends the run as `error` and stops there.

**Live reflection check.** One opt-in test makes a real, paid Anthropic
call and is excluded from the default run:

```bash
uv run pytest tests/test_learning_live.py -v -m live_reflection
```

It is the only test that proves a real model can fill the `Lesson` schema
and that `search_lessons` genuinely retrieves what got embedded — every
other reflection test stubs the client and would stay green against a
schema no model could satisfy or a Chroma query that never ran.
```

- [ ] **Step 2: Verify the README still renders sanely**

No automated test covers README prose. Read the file back (`README.md`) and confirm the new section sits between Phase 8a and Frontend with consistent heading levels (`##`) and no broken markdown (matching fenced code blocks, no stray backticks).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document Phase 9 (the learning loop) in the README"
```

---

### Task 8: Final report — fresh eval numbers, full suite, and a live manual walkthrough

**Files:**
- Create: `docs/superpowers/reports/2026-09-04-final-report.md` (adjust the date in the filename and the doc itself to whatever day this task actually executes)

**Interfaces:**
- Consumes: the state of the entire repo after Tasks 1-7 — this task runs last and reports on everything before it.
- Produces: nothing (terminal task).

**Context:** Per the parent spec's phase 10 gate, this is the project's closing deliverable: pytest green, measured eval numbers (not recalled from an earlier phase), and a manual end-to-end walkthrough actually performed, not merely described.

- [ ] **Step 1: Bring the stack up**

```bash
docker start postgres18 chroma
cd backend
.venv/Scripts/python.exe tasks.py db-up
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe tasks.py seed
```

- [ ] **Step 2: Run the full backend suite and record the exact output**

Run: `.venv/Scripts/python.exe tasks.py test`
Record the exact final line (e.g. "N passed, M deselected, 0 failed in Ts") for the report. If anything fails, stop and fix it before continuing — this task's numbers must come from a genuinely green run, not be asserted around a known failure.

- [ ] **Step 3: Run a fresh retrieval eval and record the exact numbers**

Run: `.venv/Scripts/python.exe tasks.py ingest` then `.venv/Scripts/python.exe tasks.py eval`
Record the exact Recall@5, Recall@10, MRR, and nDCG@10 numbers printed, with today's date — do not cite an earlier phase's cached numbers.

- [ ] **Step 4: Run the full frontend suite and record the exact output**

```bash
cd ../frontend
npm test
npm run typecheck
npm run build
```
Record each command's exact pass/fail summary line.

- [ ] **Step 5: Start the dev server for the walkthrough**

In one terminal: `cd backend && .venv/Scripts/python.exe tasks.py dev` (leave running).
In another: `cd frontend && npm run dev` (leave running).

- [ ] **Step 6: Perform the live walkthrough via the browser tool**

Using the browser tool against the running frontend (default `http://localhost:5173`):

1. Sign in as a seeded employee (see `backend/app/db/seed.py` for seeded credentials/pattern) and open `/chat`. Describe a specific, non-routine IT problem (e.g., a VPN certificate issue) in enough detail that it plausibly routes to a ticket rather than getting answered inline.
2. Confirm a ticket was created — check `/tickets` for the employee, or (if visible to them) note the ticket reference.
3. Sign out, sign in as the helpdesk specialist the ticket's `matched_specialization` maps to (or as an admin, if no such login is readily available in the seed data), and resolve the ticket via its resolve action, including a real resolution note describing what fixed it.
4. Sign in as admin. Open `/admin/tickets` and confirm the ticket shows `resolved`. Open `/admin/traces` (or the runs list) and find the `reflection` run triggered by the resolution — confirm it completed (`ok` or `error`, either is informative) and note its `should_record` outcome if visible.
5. If a lesson was recorded, open `/admin/lessons` and confirm it appears with the expected title/category; if the model judged it not worth recording, note that honestly rather than treating it as a failure (per the same judgment call `test_learning_live.py`'s own skip path accounts for).
6. Open `/admin/audit` and confirm the resolve action (and, if applicable, any admin actions taken during the walkthrough) appear as audit rows.

Take screenshots only where they clarify something the report's text can't (e.g., the admin lessons screen showing the new row) — this is not a screenshot gallery, it's a written account of what was actually done and actually seen.

- [ ] **Step 7: Write the report**

Create `docs/superpowers/reports/2026-09-04-final-report.md` with these sections (adjust the date to the actual execution date):

```markdown
# Final Report — Phases 0-10

**Date:** <today>

## Build summary

One line per phase, each pointing at its spec and plan under
`docs/superpowers/specs/` and `docs/superpowers/plans/`.

## Measured eval numbers

The exact Recall@5/10, MRR, and nDCG@10 numbers from Step 3, with the date
measured.

## Full test suite results

The exact backend (`tasks.py test`) and frontend (`npm test`, `npm run
typecheck`, `npm run build`) summary lines from Steps 2 and 4.

## What got hardened (Phase 10)

One paragraph per backlog item from this plan's Tasks 1-6, each citing its
fix commit hash and the command that proves it.

## Manual end-to-end walkthrough

The actual narrative from Step 6: what was clicked, signed in as whom,
what was typed, what ticket/lesson/audit rows resulted, and any honest
surprises (e.g., the model judging the walkthrough's ticket too routine
to record, if that happens) -- not a description of what the code is
*supposed* to do.
```

Fill in every section with the real numbers and the real narrative from Steps 2-6 — no placeholder values.

- [ ] **Step 8: Commit**

```bash
git add docs/superpowers/reports/2026-09-04-final-report.md
git commit -m "Add the Phase 10 final report: measured eval numbers and a live end-to-end walkthrough"
```

---

## Self-Review

**Spec coverage:**

| Design spec section | Task |
|---|---|
| §3.1 runs leak | 1 |
| §3.2 lessons.created_at index | 2 |
| §3.3 Subscription._offer | 3 |
| §3.4 alembic drift | 4 |
| §3.5 refresh-token race | 5 |
| §3.6 middleware sweep | 6 |
| §4.1 README Phase 9 section | 7 |
| §4.2 final report | 8 |
| §5 testing strategy (mutation-tested regression tests) | 1, 3, 5, 6 (2 and 4 verified via alembic check per spec's own exception) |
| §6 non-obvious risks (concurrency test realism, drift-must-not-recur, leak-fix-must-not-disturb-real-assertions) | 5, 4, 1 respectively |

No spec section is unclaimed.

**Placeholder scan:** every task carries real, complete code for its fix and its test, except Task 6, which is deliberately an audit whose target file(s) cannot be known in advance (the design spec itself frames this as open-ended in outcome, not in method) — its methodology is as concrete as every other task's steps.

**Type consistency:** `RefreshToken`, `RunTrigger.INGEST_EVAL`, `RunTrigger.REFLECTION`, `Lesson.created_at`, `ApprovalRequest`, `OutboundEmail`, `Notification` are each referenced with the exact names and shapes already confirmed against the live `app/db/models.py` and `alembic check` output during this plan's own research pass — none are invented or guessed.
