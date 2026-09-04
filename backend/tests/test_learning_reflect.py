"""Unit tests for app.learning.reflect — the traced model call and its
should_record gate. Every test here stubs the Anthropic client; the ONLY
test that proves a real model can fill Lesson is test_learning_live.py.
"""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.db.models import RunStatus, RunTrigger


def _valid_lesson_kwargs(**overrides):
    from app.learning.reflect import Lesson

    fields = dict(
        should_record=True,
        title="VPN certificate renewal blocks login",
        category="Network and VPN Support",
        situation="A user's VPN client rejected a renewed certificate.",
        what_worked="Re-importing the root CA bundle fixed it immediately.",
        what_to_do_differently="Check certificate chain validity before escalating.",
        applies_to=["vpn", "certificates"],
        confidence="high",
    )
    fields.update(overrides)
    return fields


def _valid_lesson(**overrides):
    from app.learning.reflect import Lesson
    return Lesson(**_valid_lesson_kwargs(**overrides))


class _FakeParsed:
    def __init__(self, parsed):
        self.parsed_output = parsed
        self.model = "claude-opus-5"

        class _Usage:
            input_tokens = 500
            output_tokens = 120
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

        self.usage = _Usage()


class _FakeAsyncMessages:
    def __init__(self, result=None, raises=None):
        self.calls: list[dict] = []
        self._result = result
        self._raises = raises

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return _FakeParsed(self._result)


class _FakeAsyncClient:
    def __init__(self, result=None, raises=None):
        self.messages = _FakeAsyncMessages(result, raises)


def _material():
    from app.learning.reflect import ReflectionMaterial

    # conversation_id=None, not uuid.uuid4(): build_lesson's start_run()
    # inserts through tracing's OWN committing session (app.tracing.store
    # .insert_run), independent of db_session's savepoint -- a random UUID
    # with no real, committed Conversation row trips the runs.conversation_id
    # foreign key immediately (test_admin_dossier.py's module docstring hits
    # the identical issue for build_dossier, and hard-commits a real ticket
    # chain to work around it). None is a valid value for this NULLable
    # column and none of these tests assert on the run's conversation_id, so
    # it sidesteps the FK entirely rather than standing up a real committed
    # Conversation row per test.
    return ReflectionMaterial(conversation_id=None, content="the prompt content")


class TestGatherMaterial:
    def test_includes_ticket_and_task_fields_and_conversation_transcript(self, db_session, make_ticket):
        from app.chat.service import append_message
        from app.db.models import MessageRole
        from app.learning.reflect import gather_material

        ticket = make_ticket(title="VPN keeps dropping")
        ticket.resolution = "Reset the tunnel MTU to 1400."
        db_session.commit()
        append_message(db_session, ticket.conversation_id, MessageRole.USER, [{"type": "text", "text": "My VPN keeps dropping"}])
        db_session.commit()

        material = gather_material(db_session, ticket)

        assert material.conversation_id == ticket.conversation_id
        assert "VPN keeps dropping" in material.content
        assert "Reset the tunnel MTU to 1400." in material.content
        assert ticket.matched_specialization in material.content


class TestBuildLesson:
    async def test_returns_the_parsed_lesson_and_its_run_id_on_success(self, cleanup_run):
        from app.learning.reflect import build_lesson

        client = _FakeAsyncClient(result=_valid_lesson())
        result = await build_lesson(client, _material())

        assert result.lesson.should_record is True
        assert result.lesson.title == "VPN certificate renewal blocks login"
        assert isinstance(result.run_id, uuid.UUID)
        assert client.messages.calls[0]["output_format"].__name__ == "Lesson"
        cleanup_run(result.run_id)

    async def test_records_usage_on_the_run_even_though_the_call_is_not_streamed(self, cleanup_run, db_session):
        from app.db.models import Run
        from app.learning.reflect import build_lesson

        client = _FakeAsyncClient(result=_valid_lesson())
        await build_lesson(client, _material())

        run = db_session.query(Run).filter(Run.trigger == RunTrigger.REFLECTION).order_by(Run.started_at.desc()).first()
        assert run is not None
        assert run.input_tokens == 500
        assert run.output_tokens == 120
        assert run.status == RunStatus.OK
        cleanup_run(run.id)

    async def test_a_should_record_false_lesson_still_succeeds(self, cleanup_run):
        from app.learning.reflect import build_lesson

        client = _FakeAsyncClient(result=_valid_lesson(should_record=False))
        result = await build_lesson(client, _material())

        assert result.lesson.should_record is False
        cleanup_run(result.run_id)

    async def test_a_validation_error_ends_the_run_as_error_and_raises_reflection_failed(self, cleanup_run, db_session):
        from app.db.models import Run
        from app.learning.reflect import ReflectionFailed, build_lesson

        client = _FakeAsyncClient(raises=ValidationError.from_exception_data("Lesson", []))

        with pytest.raises(ReflectionFailed):
            await build_lesson(client, _material())

        run = db_session.query(Run).filter(Run.trigger == RunTrigger.REFLECTION).order_by(Run.started_at.desc()).first()
        assert run.status == RunStatus.ERROR
        cleanup_run(run.id)

    async def test_a_response_with_no_parsed_output_is_an_error(self, cleanup_run, db_session):
        from app.db.models import Run
        from app.learning.reflect import ReflectionFailed, build_lesson

        client = _FakeAsyncClient(result=None)

        with pytest.raises(ReflectionFailed):
            await build_lesson(client, _material())

        run = db_session.query(Run).filter(Run.trigger == RunTrigger.REFLECTION).order_by(Run.started_at.desc()).first()
        assert run.status == RunStatus.ERROR
        cleanup_run(run.id)

    async def test_an_unexpected_exception_is_also_reflection_failed(self, cleanup_run, db_session):
        from app.db.models import Run
        from app.learning.reflect import ReflectionFailed, build_lesson

        client = _FakeAsyncClient(raises=RuntimeError("upstream exploded"))

        with pytest.raises(ReflectionFailed, match="upstream exploded"):
            await build_lesson(client, _material())

        run = db_session.query(Run).filter(Run.trigger == RunTrigger.REFLECTION).order_by(Run.started_at.desc()).first()
        assert run.status == RunStatus.ERROR
        cleanup_run(run.id)


class _FakeRagBackend:
    def __init__(self):
        self.upserts: list[dict] = []

    async def upsert(self, collection, ids, documents, metadatas):
        self.upserts.append({"collection": collection, "ids": ids, "documents": documents, "metadatas": metadatas})

    async def heartbeat(self):
        return True

    async def query(self, collection, query_text, where, k):
        return {"ids": [], "documents": [], "metadatas": [], "distances": []}

    async def delete(self, collection, ids):
        pass


def _committed_ticket():
    """Builds the Conversation/Run/Task/Ticket chain through a real,
    hard-committing session rather than the conftest `make_ticket` fixture.

    reflect() opens its OWN session via get_sessionmaker()() -- a genuinely
    separate connection from db_session's savepoint-scoped one. Measured:
    a ticket built through make_ticket()/db_session is invisible there --
    reflect() logs "ticket that no longer exists" and returns immediately,
    for every TestReflect test, deterministically, in isolation. This is
    the identical, already-documented gap tests/test_admin_dossier_live.py's
    `_committed_ticket` helper and tests/test_approvals_service.py's
    `pending_request` fixture already exist to work around for build_dossier
    and decide() respectively -- same fix, applied here for reflect().
    """
    from app.db.models import (
        Conversation, Run, RunStatus, RunTrigger, Severity, Task, TaskCategory,
        Ticket, TicketPriority, TicketStatus,
    )
    from app.db.session import get_sessionmaker

    Session = get_sessionmaker()
    with Session() as s:
        conv = Conversation(guest_name="Guest", guest_email="guest@example.com")
        s.add(conv)
        run = Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK)
        s.add(run)
        s.commit()

        task = Task(
            conversation_id=conv.id, user_id=None, guest_email="guest@example.com",
            title="Ticket title", category=TaskCategory.VPN_NETWORK, severity=Severity.MEDIUM,
            summary="s", affected_systems=[], evidence={}, classified_by_run_id=run.id,
        )
        s.add(task)
        s.commit()

        ticket = Ticket(
            task_id=task.id, conversation_id=conv.id,
            requester_user_id=None, requester_guest_email="guest@example.com",
            assignee_helpdesk_ref="HD-901", matched_specialization="Network and VPN Support",
            assignment_rationale="seeded by _committed_ticket", assignment_score=0.9,
            priority=TicketPriority.MEDIUM, status=TicketStatus.OPEN, title="Ticket title", body="Body",
        )
        s.add(ticket)
        s.commit()
        return ticket.id, conv.id, task.id


def _cleanup_committed_ticket(ticket_id, conv_id, task_id):
    """Deletes everything _committed_ticket hard-committed, plus whatever
    reflect() itself added on top (a Lesson row, a REFLECTION run) --
    build_lesson's start_run(conversation_id=...) stamps the REFLECTION
    run with the SAME conversation_id as the seed CHAT_TURN run, so
    filtering Run by conversation_id sweeps both in one query."""
    from app.db.models import Conversation, Lesson as DbLesson, Run, Span, Task, Ticket
    from app.db.session import get_sessionmaker

    Session = get_sessionmaker()
    with Session() as s:
        s.query(DbLesson).filter(DbLesson.ticket_id == ticket_id).delete()
        run_ids = [r.id for r in s.query(Run.id).filter(Run.conversation_id == conv_id)]
        if run_ids:
            s.query(Span).filter(Span.run_id.in_(run_ids)).delete(synchronize_session=False)
            s.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
        s.query(Ticket).filter(Ticket.id == ticket_id).delete()
        s.query(Task).filter(Task.id == task_id).delete()
        s.query(Conversation).filter(Conversation.id == conv_id).delete()
        s.commit()


class TestReflect:
    async def test_records_a_lesson_when_should_record_is_true(self, monkeypatch, tmp_path):
        import app.learning.reflect as reflect_module
        import app.learning.writer as writer_module
        from app.db.models import Lesson as DbLesson
        from app.db.session import get_sessionmaker

        ticket_id, conv_id, task_id = _committed_ticket()

        monkeypatch.setattr(writer_module, "KNOWLEDGE_LESSONS_DIR", tmp_path)
        monkeypatch.setattr(writer_module, "get_rag_backend", lambda: _FakeRagBackend())
        monkeypatch.setattr(reflect_module, "_get_client", lambda: _FakeAsyncClient(result=_valid_lesson()))

        try:
            await reflect_module.reflect(ticket_id)

            Session = get_sessionmaker()
            with Session() as s:
                lesson = s.query(DbLesson).filter(DbLesson.ticket_id == ticket_id).one_or_none()
                assert lesson is not None
                assert lesson.embedded_at is not None
        finally:
            _cleanup_committed_ticket(ticket_id, conv_id, task_id)

    async def test_records_nothing_when_should_record_is_false(self, monkeypatch):
        import app.learning.reflect as reflect_module
        from app.db.models import Lesson as DbLesson, Run, RunTrigger
        from app.db.session import get_sessionmaker

        ticket_id, conv_id, task_id = _committed_ticket()

        monkeypatch.setattr(reflect_module, "_get_client", lambda: _FakeAsyncClient(result=_valid_lesson(should_record=False)))

        try:
            await reflect_module.reflect(ticket_id)

            Session = get_sessionmaker()
            with Session() as s:
                assert s.query(DbLesson).filter(DbLesson.ticket_id == ticket_id).count() == 0
                run = s.query(Run).filter(Run.trigger == RunTrigger.REFLECTION, Run.conversation_id == conv_id).order_by(Run.started_at.desc()).first()
                assert run is not None
        finally:
            _cleanup_committed_ticket(ticket_id, conv_id, task_id)

    async def test_a_failed_reflection_never_raises(self, monkeypatch):
        import app.learning.reflect as reflect_module

        ticket_id, conv_id, task_id = _committed_ticket()
        monkeypatch.setattr(reflect_module, "_get_client", lambda: _FakeAsyncClient(raises=RuntimeError("network exploded")))

        try:
            await reflect_module.reflect(ticket_id)  # must not raise
        finally:
            _cleanup_committed_ticket(ticket_id, conv_id, task_id)

    async def test_a_failure_before_build_lesson_also_never_raises(self, monkeypatch):
        """gather_material and _get_client both run unguarded before the
        first try block in reflect() -- a gap in the brief's own Step 7
        code, where only build_lesson/create_lesson were wrapped. This
        reproduces code review's exact mutation (monkeypatching
        gather_material to raise) to prove the module docstring's "never
        raises" contract actually holds for every step, not just the two
        that happened to already be wrapped."""
        import app.learning.reflect as reflect_module

        ticket_id, conv_id, task_id = _committed_ticket()

        def _boom(db, ticket):
            raise RuntimeError("gather_material exploded")

        monkeypatch.setattr(reflect_module, "gather_material", _boom)

        try:
            await reflect_module.reflect(ticket_id)  # must not raise
        finally:
            _cleanup_committed_ticket(ticket_id, conv_id, task_id)
