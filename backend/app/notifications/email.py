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

# The approval states from which STARTING a send is legitimate.
#
# This is deliberately NARROWER than the database CHECK on
# outbound_emails.approval_status_at_send, and the two must not be made to
# match. The CHECK also permits 'failed' because the composite FK carries
# ON UPDATE CASCADE: when an execution fails, the approval moves to
# 'failed' and cascades that value into the already-written row of the send
# that failed. Widening this set to match the CHECK would let a send begin
# from an approval that was never granted; narrowing the CHECK to match
# this set would make the cascade violate its own constraint and break
# every failure path.
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
    if any(ch in address for ch in "\r\n"):
        # A newline in an address is header-injection shaped, and fnmatch
        # will not stop it -- '*@example.test' happily matches
        # 'x\r\nBcc:evil@evil.test@example.test'. Reject before it can reach
        # EmailMessage, which would raise and strand the row at 'queued'.
        return False
    candidate = address.strip().lower()
    if not candidate:
        return False
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
    try:
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = to_address
        message["Subject"] = subject
        message.set_content(body)
        row.smtp_response = _transport.send(message, to_address=to_address)
        row.status = EmailStatus.SENT
        row.sent_at = datetime.now(timezone.utc)
    except Exception as exc:  # noqa: BLE001 -- every failure is recorded, never raised
        # Header construction is inside this block deliberately. EmailMessage
        # raises ValueError on a CR/LF in any header value, and a send() that
        # raises would leave the row flushed above stuck at 'queued' forever
        # -- the one state that means "we do not know what happened".
        row.status = EmailStatus.FAILED
        row.smtp_response = f"{type(exc).__name__}: {exc}"
    db.flush()
    return row
