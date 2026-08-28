from __future__ import annotations

import uuid

from app.approvals.service import create
from app.db.models import ApprovalActionType, Conversation, RiskLevel


def test_create_inserts_pending_approval_request(db_session):
    conv = Conversation(guest_name="Guest", guest_email="g@example.com")
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)

    request = create(
        db_session, conversation_id=conv.id, task_id=None, requester_user_id=None,
        action_type=ApprovalActionType.SEND_EMAIL, action_payload={"to": "hr@northstar.example"},
        justification="User needs a copy of their offer letter.", risk_level=RiskLevel.LOW,
        agent_summary="Requesting email send to HR on user's behalf.",
    )

    assert request.request_number is not None
    assert request.status.value == "pending"


import uuid as _uuid

import pytest

from app.approvals import service as approvals_service
from app.db.models import (
    ApprovalActionType, ApprovalRequest, ApprovalStatus, AuditLog, Conversation,
    Notification, NotificationType, RiskLevel, Role, Run, Span, User,
)
from app.db.session import get_sessionmaker
from app.rbac.policy import Principal

# decide()'s approval path calls tracing.start_run(), which inserts a Run
# row -- referencing conversation_id and, when present, user_id -- through
# its OWN independently-committing connection (app/tracing/spans.py's
# module docstring). A Conversation/User row created only through
# db_session is never more than a SAVEPOINT release (see this file's
# db_session fixture), so it is invisible to that separate connection's FK
# check under READ COMMITTED: every approve=True test below would fail
# immediately with `IntegrityError ... runs_conversation_id_fkey` the
# instant decide() calls start_run(). This is the identical, documented gap
# tests/test_agent_loop.py's `_conversation` helper already fixes for
# run_turn(). `pending_request` below creates the Conversation and
# requester User through a real, hard-committing session instead, so both
# rows are visible to every connection before decide() ever runs, and
# registers their ids here for `_sweep_hard_committed_rows_after_module` to
# delete once every test in this module has released its db_session locks
# on them.
_hard_committed_rows: list[tuple[_uuid.UUID, _uuid.UUID | None]] = []


@pytest.fixture(scope="module", autouse=True)
def _sweep_hard_committed_rows_after_module():
    """Deletes the Conversation/User/Run/Span rows `pending_request` hard-
    commits, but only after every test in this module has finished. Each
    test's own db_session holds a FOR KEY SHARE lock on its Conversation/
    User row -- taken when ApprovalRequest was inserted referencing them --
    for as long as that test's transaction stays open, so deleting them any
    earlier would hang waiting on a lock that only releases at that test's
    own teardown. Mirrors test_agent_loop.py's identically-motivated
    module-scoped final sweep."""
    yield
    Session = get_sessionmaker()
    with Session() as session:
        conversation_ids = [c for c, _u in _hard_committed_rows]
        user_ids = [u for _c, u in _hard_committed_rows if u is not None]
        if conversation_ids:
            run_ids = [r for (r,) in session.query(Run.id).filter(Run.conversation_id.in_(conversation_ids))]
            if run_ids:
                session.query(Span).filter(Span.run_id.in_(run_ids)).delete(synchronize_session=False)
                session.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
            session.query(Conversation).filter(Conversation.id.in_(conversation_ids)).delete(synchronize_session=False)
        if user_ids:
            session.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        session.commit()


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
    Session = get_sessionmaker()
    with Session() as hard_session:
        requester = User(
            username=f"u{_uuid.uuid4().hex[:10]}", email=f"{_uuid.uuid4().hex[:10]}@northstar.example",
            full_name="Test User", password_hash="x", role=Role.EMPLOYEE, is_active=True,
        )
        hard_session.add(requester)
        conv = Conversation(guest_name="G", guest_email="g@northstar.example")
        hard_session.add(conv)
        hard_session.commit()
        requester_id = requester.id
        conversation_id = conv.id
    _hard_committed_rows.append((conversation_id, requester_id))

    row = ApprovalRequest(
        conversation_id=conversation_id, task_id=None, requester_user_id=requester_id,
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


def test_a_handler_that_fails_inside_the_database_still_lands_in_failed(
    db_session, admin_principal, pending_request, monkeypatch,
):
    """A handler can fail in two very different ways. A plain exception is
    harmless -- `execute` catches it and decide() records `failed`. A
    DATABASE error is not: it leaves the session needing a rollback, so
    decide()'s next flush (the one writing `failed` and its reason) raises
    PendingRollbackError, the whole decision rolls back, and the approval
    sits at `pending` while the admin gets an opaque 500 that every retry
    reproduces.

    Reproduced for real, not simulated: the handler here writes an
    OutboundEmail whose subject exceeds the column's String(500), which is
    exactly what an unbounded model-authored send_email payload used to do
    at the pre-send flush.
    """
    from app.approvals import executor
    from app.db.models import EmailStatus, OutboundEmail

    def _fails_inside_the_database(db, approval, payload):
        db.add(OutboundEmail(
            approval_request_id=approval.id, approval_status_at_send=approval.status,
            to_address="ops@northstar.example", subject="x" * 600, body="b",
            status=EmailStatus.QUEUED,
        ))
        db.flush()
        return {}

    monkeypatch.setitem(executor.HANDLERS, ApprovalActionType.RESET_CREDENTIAL, _fails_inside_the_database)
    _admin, principal = admin_principal

    result = approvals_service.decide(db_session, principal, pending_request.id, approve=True, note="")

    assert result.status is ApprovalStatus.FAILED
    assert result.execution_result["reason"] == "handler_failed"
    assert "DataError" in result.execution_result["detail"]
    # The decision itself committed, so the admin is not stuck retrying.
    assert result.decided_at is not None
    assert db_session.query(OutboundEmail).filter(
        OutboundEmail.approval_request_id == pending_request.id,
    ).count() == 0


def test_a_disclosure_does_not_survive_a_failed_decision(monkeypatch):
    """Finding 2. The disclose handler used to call chat.service.append_message,
    which COMMITS. That commit landed mid-decide(), with the approval at
    `approved` and executed_at/execution_result still NULL -- so a failure
    afterwards left the row stuck there forever (not `pending`, so every
    retry 409s; not terminal, so nothing records what happened) AFTER the
    restricted information had already been disclosed.

    Runs on real, committing sessions rather than the savepoint-scoped
    db_session, because the whole point is what survives a transaction that
    genuinely fails.
    """
    from app.db.models import Message

    Session = get_sessionmaker()
    with Session() as setup:
        conv = Conversation(guest_name="G", guest_email="g@northstar.example")
        setup.add(conv)
        setup.commit()
        conversation_id = conv.id
        approval = ApprovalRequest(
            conversation_id=conversation_id, task_id=None, requester_user_id=None,
            action_type=ApprovalActionType.DISCLOSE_RESTRICTED_INFORMATION,
            action_payload={"disclosure": "The build server root password rotates on Fridays."},
            justification="j", risk_level=RiskLevel.HIGH, agent_summary="a",
            status=ApprovalStatus.PENDING,
        )
        setup.add(approval)
        setup.commit()
        approval_id = approval.id

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure after the handler ran")

    # decide() notifies AFTER the handler has run and before it commits --
    # the exact window in which the old code had already committed the
    # disclosure.
    monkeypatch.setattr("app.notifications.service.notify", _boom)

    principal = Principal(
        kind="user", user_id=None, role="admin", clearance=None,
        department=None, employee_ref=None, helpdesk_ref=None,
    )
    try:
        with Session() as session:
            with pytest.raises(RuntimeError):
                approvals_service.decide(session, principal, approval_id, approve=True, note="")
            session.rollback()

        with Session() as check:
            messages = check.query(Message).filter(Message.conversation_id == conversation_id).all()
            assert messages == [], "the disclosure outlived the transaction that failed"
            assert check.get(ApprovalRequest, approval_id).status is ApprovalStatus.PENDING
    finally:
        with Session() as cleanup:
            cleanup.query(Message).filter(Message.conversation_id == conversation_id).delete(synchronize_session=False)
            cleanup.query(AuditLog).filter(AuditLog.target_id == str(approval_id)).delete(synchronize_session=False)
            cleanup.query(ApprovalRequest).filter(ApprovalRequest.id == approval_id).delete(synchronize_session=False)
            run_ids = [r for (r,) in cleanup.query(Run.id).filter(Run.conversation_id == conversation_id)]
            if run_ids:
                cleanup.query(Span).filter(Span.run_id.in_(run_ids)).delete(synchronize_session=False)
                cleanup.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
            cleanup.query(Conversation).filter(Conversation.id == conversation_id).delete(synchronize_session=False)
            cleanup.commit()


def test_two_concurrent_decisions_execute_the_action_exactly_once(monkeypatch):
    """Finding 1, reproduced as a genuine race: two threads, two real
    sessions, one approval.

    The idempotency guard used to be a plain SELECT, a Python-side status
    check, and then an UPDATE. Both callers read `pending`; the second one
    blocked on the row lock the first took at its own UPDATE and then simply
    carried on once the first committed, because nothing re-read the status
    it had checked before waiting. On a send_email approval that is two real
    SMTP sends and two `outbound_emails` rows -- reachable from a
    double-clicked Approve button or a proxy retrying the POST.

    The overlap is made deterministic rather than hoped for: the transport
    holds the winner's transaction -- and therefore its row lock -- open
    while the second decision is started. With SELECT ... FOR UPDATE the
    loser blocks BEFORE it reads, so it sees the winner's committed
    `executed` and raises NotPending. Without it, the loser sends again.

    Each thread calls `get()` on its own session before `decide()`, exactly
    as app/admin/router.py's `decide_approval` does (it checks existence via
    `get()`, then calls `decide()` on that same `db`). That first read is
    what populates the session's identity map -- without it, `decide()`'s
    query is this session's first sight of the row and there is no stale
    instance for `_load_for_decision`'s `populate_existing()` to matter for.
    Skip this step and the test would still pass with `populate_existing()`
    deleted, verifying nothing about the one line that actually closes the
    hole.
    """
    import threading
    import time

    from app.db.models import EmailStatus, Message, OutboundEmail
    from app.notifications import email as email_module

    sent: list[str] = []
    first_send_started = threading.Event()

    class _SlowTransport:
        def send(self, message, *, to_address: str) -> str:
            sent.append(to_address)
            first_send_started.set()
            time.sleep(1.0)
            return "250 OK"

    monkeypatch.setattr(email_module, "_transport", _SlowTransport())
    monkeypatch.setattr(email_module, "allowlist_patterns", lambda: ["ops@northstar.example"])

    Session = get_sessionmaker()
    with Session() as setup:
        conv = Conversation(guest_name="G", guest_email="g@northstar.example")
        setup.add(conv)
        setup.commit()
        conversation_id = conv.id
        approval = ApprovalRequest(
            conversation_id=conversation_id, task_id=None, requester_user_id=None,
            action_type=ApprovalActionType.SEND_EMAIL,
            action_payload={"to_address": "ops@northstar.example", "subject": "s", "body": "b"},
            justification="j", risk_level=RiskLevel.HIGH, agent_summary="a",
            status=ApprovalStatus.PENDING,
        )
        setup.add(approval)
        setup.commit()
        approval_id = approval.id

    principal = Principal(
        kind="user", user_id=None, role="admin", clearance=None,
        department=None, employee_ref=None, helpdesk_ref=None,
    )
    outcomes: list[tuple[str, str | None]] = []
    outcomes_lock = threading.Lock()

    def _decide() -> None:
        with Session() as session:
            try:
                # Load the row into THIS session's identity map before
                # deciding, mirroring app/admin/router.py's
                # get()-then-decide() on one `db`. This is what makes
                # `_load_for_decision`'s `populate_existing()` load-bearing
                # for this test: without it, `decide()`'s locked SELECT
                # would be the session's first read of the row, so even a
                # stale, unrefreshed identity-map instance couldn't be
                # returned -- there wouldn't be one yet. Do not remove this
                # call; it looks redundant with `decide()`'s own query but
                # is the entire point of the test.
                #
                # The reference is kept (not just checked and discarded like
                # the router's `if approvals.get(...) is None:`), because
                # the Session identity map holds only a WEAK reference to
                # each loaded instance: a discarded result is eligible for
                # garbage collection immediately, which silently drops it
                # from the identity map before `decide()` runs and makes the
                # whole scenario this test exists for disappear depending on
                # GC timing -- exactly the kind of flakiness that would make
                # this test worthless as a guard. Holding `loaded` for the
                # rest of this function's scope keeps the instance (and so
                # the stale-read hazard) alive deterministically.
                loaded = approvals_service.get(session, approval_id)  # noqa: F841 -- kept alive, see comment above
                decided = approvals_service.decide(session, principal, approval_id, approve=True, note="")
                outcome = ("decided", decided.status.value)
            except approvals_service.NotPending:
                outcome = ("not_pending", None)
            except Exception as exc:  # noqa: BLE001 -- surfaced by the assertions below
                outcome = ("error", f"{type(exc).__name__}: {exc}")
        with outcomes_lock:
            outcomes.append(outcome)

    try:
        winner = threading.Thread(target=_decide, name="approval-decide-1")
        winner.start()
        assert first_send_started.wait(timeout=15), "the first decision never reached the transport"
        loser = threading.Thread(target=_decide, name="approval-decide-2")
        loser.start()
        winner.join(timeout=60)
        loser.join(timeout=60)
        assert not winner.is_alive() and not loser.is_alive(), "a decision thread never finished"

        assert sorted(outcomes) == [("decided", "executed"), ("not_pending", None)], outcomes
        assert sent == ["ops@northstar.example"], f"the approved action executed {len(sent)} times"
        with Session() as check:
            rows = check.query(OutboundEmail).filter(
                OutboundEmail.approval_request_id == approval_id,
            ).all()
            assert len(rows) == 1, f"{len(rows)} outbound_emails rows for one approval"
            assert rows[0].status is EmailStatus.SENT
    finally:
        with Session() as cleanup:
            cleanup.query(OutboundEmail).filter(
                OutboundEmail.approval_request_id == approval_id,
            ).delete(synchronize_session=False)
            cleanup.query(AuditLog).filter(AuditLog.target_id == str(approval_id)).delete(synchronize_session=False)
            cleanup.query(ApprovalRequest).filter(ApprovalRequest.id == approval_id).delete(synchronize_session=False)
            run_ids = [r for (r,) in cleanup.query(Run.id).filter(Run.conversation_id == conversation_id)]
            if run_ids:
                cleanup.query(Span).filter(Span.run_id.in_(run_ids)).delete(synchronize_session=False)
                cleanup.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
            cleanup.query(Message).filter(Message.conversation_id == conversation_id).delete(synchronize_session=False)
            cleanup.query(Conversation).filter(Conversation.id == conversation_id).delete(synchronize_session=False)
            cleanup.commit()


def test_list_for_admin_filters_by_status(db_session, admin_principal, pending_request):
    pending = approvals_service.list_for_admin(db_session, status=ApprovalStatus.PENDING)
    assert pending_request.id in {r.id for r in pending}
    denied = approvals_service.list_for_admin(db_session, status=ApprovalStatus.DENIED)
    assert pending_request.id not in {r.id for r in denied}
