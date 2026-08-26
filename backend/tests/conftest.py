import subprocess
import sys
from pathlib import Path

import chromadb
import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_engine
from app.rag.direct_client import _parse_chroma_url

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def _migrated_database():
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        check=True,
    )
    yield


@pytest.fixture()
def drop_chroma_collection():
    """Test-hygiene-only helper: deletes a Chroma collection directly via a
    raw chromadb.HttpClient, bypassing RagBackend (neither RagBackend nor
    either concrete backend exposes a delete_collection method -- adding one
    to the public interface is out of scope for test cleanup). Collections
    live on the same underlying Chroma server regardless of which backend
    (direct or mcp) created them, so this works for both. Tests should call
    the returned function in their `finally` blocks after deleting documents,
    so repeated suite runs don't leak orphaned test_* collections into the
    shared Chroma instance."""
    settings = get_settings()
    host, port = _parse_chroma_url(settings.chroma_url)
    client = chromadb.HttpClient(host=host, port=port)

    def _drop(name: str) -> None:
        try:
            client.delete_collection(name)
        except Exception:
            pass  # already gone / never created -- fine for cleanup

    return _drop


@pytest.fixture()
def cleanup_run():
    """Test-hygiene-only helper: app.tracing.store opens its own committed
    sessions for every write (by design -- a trace must survive even if
    the caller's own business transaction rolls back), so tracing test
    data is NOT cleaned up by db_session's rollback the way ordinary test
    data is. Call the returned function with a run_id in a `finally` block
    to delete that run's spans then the run row itself, mirroring exactly
    how drop_chroma_collection cleans up test collections above."""
    from app.db.models import Run, Span
    from app.db.session import get_sessionmaker

    def _cleanup(run_id) -> None:
        Session = get_sessionmaker()
        with Session() as session:
            session.query(Span).filter(Span.run_id == run_id).delete()
            session.query(Run).filter(Run.id == run_id).delete()
            session.commit()

    return _cleanup


@pytest.fixture()
def db_session():
    engine = get_engine()
    connection = engine.connect()
    outer_transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()


from fastapi.testclient import TestClient

from app.db.session import get_db as _get_db


@pytest.fixture()
def client(db_session):
    from app.main import app

    def _override_get_db():
        yield db_session

    app.dependency_overrides[_get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def make_ticket(db_session):
    """Ticket.task_id and Ticket.conversation_id are NOT NULL foreign keys,
    and Task.classified_by_run_id is one too -- a Ticket cannot be built in
    isolation. Creates the whole Conversation/Run/Task chain through
    db_session (NOT tracing.start_run(), which commits on its own connection
    and would deadlock against db_session's held transaction) so everything
    rolls back together at teardown."""
    import uuid as _uuid

    from app.db.models import (
        Conversation, Run, RunStatus, RunTrigger, Severity, Task, TaskCategory,
        Ticket, TicketPriority, TicketStatus,
    )

    def _make(
        *,
        assignee_helpdesk_ref: str = "HD-901",
        status: TicketStatus = TicketStatus.OPEN,
        priority: TicketPriority = TicketPriority.MEDIUM,
        category: TaskCategory = TaskCategory.VPN_NETWORK,
        requester_user_id: _uuid.UUID | None = None,
        requester_guest_email: str | None = None,
        title: str = "Ticket title",
    ) -> Ticket:
        conv = Conversation(guest_name="Guest", guest_email="guest@example.com")
        db_session.add(conv)
        run = Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK)
        db_session.add(run)
        db_session.commit()

        task = Task(
            conversation_id=conv.id, user_id=None, guest_email="guest@example.com",
            title=title, category=category, severity=Severity.MEDIUM, summary="s",
            affected_systems=[], evidence={}, classified_by_run_id=run.id,
        )
        db_session.add(task)
        db_session.commit()

        ticket = Ticket(
            task_id=task.id, conversation_id=conv.id,
            requester_user_id=requester_user_id, requester_guest_email=requester_guest_email,
            assignee_helpdesk_ref=assignee_helpdesk_ref, matched_specialization="Network and VPN Support",
            assignment_rationale="seeded by make_ticket", assignment_score=0.9,
            priority=priority, status=status, title=title, body="Body",
        )
        db_session.add(ticket)
        db_session.commit()
        db_session.refresh(ticket)
        return ticket

    return _make
