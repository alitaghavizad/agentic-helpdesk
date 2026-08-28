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

pytestmark = pytest.mark.live_smtp


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
