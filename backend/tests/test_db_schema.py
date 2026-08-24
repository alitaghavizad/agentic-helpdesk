import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.db.models import Conversation, Role, User
from app.db.session import get_engine, get_sessionmaker

EXPECTED_TABLES = {
    "users", "refresh_tokens", "conversations", "messages", "attachments",
    "tasks", "tickets", "approval_requests", "outbound_emails",
    "runs", "spans", "audit_log", "usage_counters", "lessons", "notifications",
}


def test_all_tables_exist_after_migration():
    inspector = sa.inspect(get_engine())
    tables = set(inspector.get_table_names())
    missing = EXPECTED_TABLES - tables
    assert not missing, f"Missing tables after migration: {missing}"


def test_conversations_requester_constraint_rejects_empty_requester():
    Session = get_sessionmaker()
    with Session() as session:
        try:
            session.execute(
                Conversation.__table__.insert().values(id=uuid.uuid4(), status="active")
            )
            session.commit()
            assert False, "expected IntegrityError for missing requester"
        except IntegrityError:
            session.rollback()


def test_conversations_requester_constraint_rejects_both_user_and_guest(db_session):
    # Use a real, FK-satisfying user so the IntegrityError we assert on can
    # only come from the requester check constraint, not a missing FK.
    user_id = uuid.uuid4()
    db_session.execute(
        User.__table__.insert().values(
            id=user_id,
            username=f"conv-both-{user_id.hex[:8]}",
            email=f"conv-both-{user_id.hex[:8]}@example.com",
            full_name="Test User",
            password_hash="x",
            role=Role.EMPLOYEE,
        )
    )
    db_session.flush()

    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(
            Conversation.__table__.insert().values(
                id=uuid.uuid4(),
                status="active",
                user_id=user_id,
                guest_name="Jane Doe",
                guest_email="jane@example.com",
            )
        )
        db_session.flush()
    assert "ck_conversations_requester_present" in str(exc_info.value)
    db_session.rollback()
