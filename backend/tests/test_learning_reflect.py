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
