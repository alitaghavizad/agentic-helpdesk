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
from datetime import datetime, timezone

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


@event.listens_for(Session, "after_rollback")
def _drop_after_hard_rollback(session: Session) -> None:
    """A session is long-lived and can run more than one transaction, so a
    rollback must clear any notifications staged during the transaction
    that just died -- otherwise they would wrongly ride along on that
    session's *next* successful commit. Both `after_soft_rollback` (fires
    for every rollback, including a plain nested/savepoint release) and
    `after_rollback` (fires for a full transaction rollback) are registered
    because it is cheap to be defensive here and the two fire in different
    combinations across SQLAlchemy's rollback paths; `_discard_pending` is
    idempotent, so registering both is safe rather than redundant-risky."""
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
    row = db.query(Notification).filter(
        Notification.id == notification_id, Notification.user_id == user_id,
    ).one_or_none()
    if row is None:
        return None
    if row.read_at is None:
        row.read_at = datetime.now(timezone.utc)
        db.flush()
    return row
