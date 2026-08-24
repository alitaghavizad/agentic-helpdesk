import uuid

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.db.models import Conversation
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
