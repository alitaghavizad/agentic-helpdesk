from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

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
        # requester_user_id may deliberately name a user that no longer
        # exists, simulating a hard-deleted account -- the FK on this column
        # is NOT DEFERRABLE, so Postgres would reject the literal INSERT
        # before the executor ever ran. The point of a test using such an id
        # is the executor's OWN re-validation, not Postgres's constraint, so
        # trigger enforcement (including FK checks) is suspended for this
        # one flush.
        db_session.execute(text("SET session_replication_role = replica"))
        try:
            db_session.flush()
        finally:
            db_session.execute(text("SET session_replication_role = DEFAULT"))
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
