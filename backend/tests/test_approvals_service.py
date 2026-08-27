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


def test_list_for_admin_filters_by_status(db_session, admin_principal, pending_request):
    pending = approvals_service.list_for_admin(db_session, status=ApprovalStatus.PENDING)
    assert pending_request.id in {r.id for r in pending}
    denied = approvals_service.list_for_admin(db_session, status=ApprovalStatus.DENIED)
    assert pending_request.id not in {r.id for r in denied}
