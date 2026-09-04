"""Makes ONE real Claude call through app.learning.reflect.build_lesson.
Excluded from the default run by the live_reflection marker.

This is the ONLY thing that proves a real model can fill Lesson and that
search_lessons genuinely retrieves what got embedded. Every other test in
this phase stubs the client and proves the assembly and error handling
around it -- necessary, but they would all stay green against a schema no
model can satisfy, or a Chroma query that never actually ran. The phase
report must cite this run, not those.

Builds its ticket through a committing session rather than the conftest
make_ticket fixture's savepoint, mirroring test_admin_dossier_live.py:
build_lesson calls start_run(conversation_id=...), which inserts on the
tracing store's own connection, invisible to a row that only exists inside
db_session's savepoint under READ COMMITTED.
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.db.session import get_sessionmaker

pytestmark = pytest.mark.live_reflection

_committed: dict[str, list[uuid.UUID]] = {
    "conversations": [], "runs": [], "tasks": [], "tickets": [], "lessons": [],
}
# Populated with the real path create_lesson wrote to, so the module-scoped
# sweep below can remove exactly the file(s) this test created and nothing
# else -- other lessons may already live in KNOWLEDGE_LESSONS_DIR from real
# use of the app.
_written_files: list[str] = []


@pytest.fixture(scope="module", autouse=True)
def _sweep_committed_rows_after_module():
    yield
    from app.db.models import Conversation, Lesson, Message, Run, Span, Task, Ticket

    Session = get_sessionmaker()
    try:
        with Session() as s:
            lesson_ids = _committed["lessons"]
            if lesson_ids:
                s.query(Lesson).filter(Lesson.id.in_(lesson_ids)).delete(synchronize_session=False)
            conv_ids = _committed["conversations"]
            if conv_ids:
                run_ids = [r.id for r in s.query(Run.id).filter(Run.conversation_id.in_(conv_ids)).all()]
                if run_ids:
                    s.query(Span).filter(Span.run_id.in_(run_ids)).delete(synchronize_session=False)
                s.query(Ticket).filter(Ticket.conversation_id.in_(conv_ids)).delete(synchronize_session=False)
                s.query(Task).filter(Task.conversation_id.in_(conv_ids)).delete(synchronize_session=False)
                if run_ids:
                    s.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
                s.query(Message).filter(Message.conversation_id.in_(conv_ids)).delete(synchronize_session=False)
                s.query(Conversation).filter(Conversation.id.in_(conv_ids)).delete(synchronize_session=False)
            # The CHAT_TURN run started below to satisfy search_lessons_
            # handler's span() precondition carries no conversation_id, so
            # the conv_ids join above cannot reach it -- swept explicitly.
            extra_run_ids = _committed["runs"]
            if extra_run_ids:
                s.query(Span).filter(Span.run_id.in_(extra_run_ids)).delete(synchronize_session=False)
                s.query(Run).filter(Run.id.in_(extra_run_ids)).delete(synchronize_session=False)
            s.commit()
    finally:
        from app.learning.writer import KNOWLEDGE_LESSONS_DIR
        for path in _written_files:
            try:
                (KNOWLEDGE_LESSONS_DIR / path).unlink(missing_ok=True)
            except Exception:
                pass

        # The Lesson row is gone from Postgres, but nothing in the app ever
        # calls RagBackend.delete() in production -- lessons are archived,
        # never hard-deleted, by design (writer.py's module docstring,
        # design D7). A test that hard-deletes the row must clean up its
        # Chroma counterpart itself, or every live-gate run leaves one more
        # orphaned, permanently status=active document in the real
        # "lessons" collection -- the exact contamination class a
        # whole-branch review found (and fixed, for a different set of
        # tests) elsewhere in this phase.
        if _committed["lessons"]:
            import asyncio

            from app.db.models import Run, RunStatus, RunTrigger
            from app.rag.backend import get_rag_backend
            from app.tracing.spans import end_run, start_run

            handle = start_run(RunTrigger.CHAT_TURN)
            try:
                asyncio.run(get_rag_backend().delete("lessons", [str(lid) for lid in _committed["lessons"]]))
            except Exception:
                pass
            finally:
                try:
                    end_run(handle, status=RunStatus.OK)
                except Exception:
                    pass
                with get_sessionmaker()() as cleanup_session:
                    cleanup_session.query(Span).filter(Span.run_id == handle.run_id).delete()
                    cleanup_session.query(Run).filter(Run.id == handle.run_id).delete()
                    cleanup_session.commit()


def _build_committed_ticket():
    from app.db.models import (
        Conversation, ResolutionPath, Run, RunStatus, RunTrigger, Severity, Task,
        TaskCategory, Ticket, TicketPriority, TicketStatus,
    )

    Session = get_sessionmaker()
    with Session() as s:
        conv = Conversation(guest_name="Live Test Guest", guest_email="livetest@example.com")
        s.add(conv)
        s.commit()
        _committed["conversations"].append(conv.id)

        run = Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK)
        s.add(run)
        s.commit()

        task = Task(
            conversation_id=conv.id, guest_email="livetest@example.com",
            title="VPN client rejects renewed certificate",
            category=TaskCategory.VPN_NETWORK, severity=Severity.MEDIUM,
            summary="User's VPN client rejected a freshly renewed certificate after IT rotated the root CA.",
            affected_systems=["vpn-gateway"], evidence={}, classified_by_run_id=run.id,
            resolution_path=ResolutionPath.TICKETED,
        )
        s.add(task)
        s.commit()

        ticket = Ticket(
            task_id=task.id, conversation_id=conv.id, requester_guest_email="livetest@example.com",
            assignee_helpdesk_ref="HD-901", matched_specialization="Network and VPN Support",
            assignment_rationale="live reflection test fixture", assignment_score=0.9,
            priority=TicketPriority.MEDIUM, status=TicketStatus.ASSIGNED,
            title="VPN client rejects renewed certificate", body="User cannot connect after cert rotation.",
        )
        s.add(ticket)
        s.commit()
        _committed["tickets"].append(ticket.id)

        from app.chat.service import append_message
        from app.db.models import MessageRole
        append_message(s, conv.id, MessageRole.USER, [{"type": "text", "text": "My VPN client rejects the new certificate the helpdesk just issued."}])
        append_message(s, conv.id, MessageRole.ASSISTANT, [{"type": "text", "text": "Re-importing the updated root CA bundle into the client's trust store resolved it."}])
        s.commit()

        s.refresh(ticket)
        return ticket.id


async def test_a_real_ticket_resolution_produces_a_retrievable_lesson():
    from app.agent.tools.knowledge import SearchLessonsArgs, search_lessons_handler
    from app.db.models import RunStatus, RunTrigger
    from app.learning.reflect import gather_material, build_lesson, _get_client
    from app.learning.writer import create_lesson
    from app.tracing.spans import end_run, start_run

    ticket_id = _build_committed_ticket()

    Session = get_sessionmaker()
    with Session() as db:
        from app.db.models import Ticket
        ticket = db.get(Ticket, ticket_id)
        material = gather_material(db, ticket)
        client = _get_client()

        result = await build_lesson(client, material)
        # should_record is the model's judgment call, not this test's --
        # a genuinely routine-sounding fixture COULD come back false. If it
        # does, this test cannot proceed to prove retrieval and must say so
        # rather than fail confusingly on a lesson that was never written.
        if not result.lesson.should_record:
            pytest.skip(
                f"the model judged this fixture ticket not worth recording "
                f"(confidence={result.lesson.confidence}) -- rerun, or adjust "
                f"the fixture in _build_committed_ticket to describe a less "
                f"routine-sounding resolution"
            )

        db_lesson = await create_lesson(db, ticket=ticket, lesson=result.lesson, run_id=result.run_id)
        db.commit()
        _committed["lessons"].append(db_lesson.id)
        _written_files.append(db_lesson.file_path)

        assert db_lesson.embedded_at is not None
        assert os.path.exists(db_lesson.file_path)

    # search_lessons_handler's real backend (McpChromaBackend, the default
    # chroma_backend="mcp") wraps its Chroma call in a tracing span, and
    # span() hard-requires an active Run -- there is none here otherwise,
    # unlike production where the agent's own tool loop already has a
    # CHAT_TURN run active for the whole turn a tool call happens inside.
    handle = start_run(RunTrigger.CHAT_TURN)
    _committed["runs"].append(handle.run_id)
    try:
        search_result = await search_lessons_handler(
            principal=None, db=None, args=SearchLessonsArgs(query="VPN certificate rejected after renewal"),
        )
    finally:
        end_run(handle, status=RunStatus.OK)

    assert any(
        f"lessons/{db_lesson.id}" in wrapped or str(db_lesson.id) in wrapped
        for wrapped in search_result["lessons"]
    ), f"the newly-embedded lesson was not retrieved: {search_result}"
