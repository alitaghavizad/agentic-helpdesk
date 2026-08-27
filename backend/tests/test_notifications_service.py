from __future__ import annotations

import uuid

import pytest

from app.db.models import NotificationType, Role, User
from app.notifications import broker, service


@pytest.fixture()
def user(db_session):
    """full_name is NOT NULL on User with no default (see the project's
    recorded Postgres fixture gotchas) -- it must be supplied explicitly or
    the flush below raises IntegrityError."""
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
