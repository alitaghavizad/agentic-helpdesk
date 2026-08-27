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
