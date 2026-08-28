from __future__ import annotations

import smtplib

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


@pytest.mark.parametrize("bad", ["x\r\nBcc:evil@evil.test@northstar.example", "x\nevil@northstar.example"])
def test_a_recipient_containing_a_newline_is_rejected(bad):
    """fnmatch does not anchor the address side, so a wildcard pattern would
    otherwise match a header-injection-shaped address."""
    assert email_module.is_allowed_recipient(bad, ["*@northstar.example"]) is False


def test_a_whitespace_only_recipient_is_rejected():
    assert email_module.is_allowed_recipient("   ", ["*@northstar.example"]) is False


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


def test_a_subject_with_a_newline_is_recorded_as_failed_not_raised(db_session, approved_request, transport):
    """The row is flushed before the socket opens, so anything that raises
    after that point must still land the row in a terminal state. A row left
    at 'queued' is the one outcome that tells us nothing."""
    row = email_module.send(
        db_session, approval=approved_request,
        to_address="ops@northstar.example",
        subject="hello\r\nBcc: evil@evil.test", body="Body",
    )
    assert row.status is EmailStatus.FAILED
    assert "ValueError" in row.smtp_response
    assert transport.sent == [], "the socket must not open for a malformed message"


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
