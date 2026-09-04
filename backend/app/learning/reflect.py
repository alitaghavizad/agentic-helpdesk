"""The traced reflection call: decides whether a ticket's resolution
taught something worth keeping (design spec 13). Knows nothing about
writing files or embeddings -- that's writer.py's job, wired together by
reflect() at the bottom of this module (Task 3).

Deliberately async, unlike the dossier's sync build_dossier -- see design
decision D2. span()'s context-manager form is async-only, so this is the
only way a reflection call gets correct cost/token accounting on its Run.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.db.models import Message, MessageRole, RunStatus, RunTrigger, Task, Ticket
from app.tracing.spans import SpanKind, end_run, span, start_run

logger = logging.getLogger(__name__)

# Matches build_dossier's model choice for the same reason: reflection is
# doing the same kind of reasoning about a resolved incident that the
# dossier does, and two model constants drifting apart silently helps no
# one. See the plan's Global Constraints for why no effort/output_config
# kwarg is set here -- messages.parse never takes one in this codebase.
_MODEL = "claude-opus-5"
_MAX_TOKENS = 2000

_SYSTEM_PROMPT = """You are reflecting on a just-resolved helpdesk ticket to decide whether it taught a durable, reusable lesson.

Most tickets are routine and teach nothing new -- a password reset, a re-issued badge, a standard provisioning request. For those, set should_record to false and leave the other fields as brief placeholders; they will not be used.

Only set should_record to true when the resolution reveals something a future agent handling a SIMILAR problem would genuinely benefit from knowing -- a non-obvious root cause, a fix that worked when the obvious one didn't, a pitfall worth flagging. A lesson recorded from every routine ticket poisons future retrieval with noise, which is worse than recording nothing."""


class Lesson(BaseModel):
    should_record: bool
    title: str
    category: str
    situation: str
    what_worked: str
    what_to_do_differently: str
    applies_to: list[str]
    confidence: Literal["low", "medium", "high"]


class ReflectionFailed(RuntimeError):
    pass


@dataclass
class ReflectionMaterial:
    conversation_id: uuid.UUID | None
    content: str


@dataclass
class LessonWithRun:
    """Carries the parsed Lesson together with the id of the Run that
    produced it. Lesson.created_by_run_id (app.db.models) is a NOT NULL
    foreign key -- a caller writing the lesson to disk needs a real run id,
    not a freshly generated, untracked UUID that Postgres would reject at
    commit."""
    lesson: "Lesson"
    run_id: uuid.UUID


def gather_material(db: Session, ticket: Ticket) -> ReflectionMaterial:
    """Reads everything the reflection prompt needs: the ticket's own
    fields, its Task, the routing decision that assigned it, and the
    conversation that produced it -- not the full span tree, which is the
    dossier's job for a different reader (design decision D9). `task_id` is
    a real NOT NULL FK to `tasks.id`, so `.one()` is correct here, not a
    defensive `.one_or_none()`: a ticket with no backing task is a data
    integrity violation, not a case to silently degrade the prompt for."""
    task = db.query(Task).filter(Task.id == ticket.task_id).one()
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == ticket.conversation_id, Message.role != MessageRole.SYSTEM)
        .order_by(Message.created_at.asc())
        .all()
    )
    transcript = "\n\n".join(f"[{m.role.value}] {m.content}" for m in messages)

    content = (
        f"Ticket TCK-{ticket.ticket_number:06d}: {ticket.title}\n"
        f"Priority: {ticket.priority.value}\n"
        f"Assigned to: {ticket.assignee_helpdesk_ref} (matched specialization: {ticket.matched_specialization})\n"
        f"Assignment rationale: {ticket.assignment_rationale}\n\n"
        f"Task category: {task.category.value}\n"
        f"Task severity: {task.severity.value}\n"
        f"Task summary: {task.summary}\n"
        f"Affected systems: {', '.join(task.affected_systems)}\n\n"
        f"Ticket body:\n{ticket.body}\n\n"
        f"Resolution:\n{ticket.resolution}\n\n"
        f"Conversation transcript:\n{transcript}"
    )
    return ReflectionMaterial(conversation_id=ticket.conversation_id, content=content)


def _end_run_quietly(handle, *, status: RunStatus, error: str | None = None) -> None:
    """Same swallow-and-log contract as the dossier's identically-named
    helper: tracing is observability, not the product, and must never turn
    an already-decided, already-billed model call into a lost result."""
    try:
        end_run(handle, status=status, error=error)
    except Exception:  # noqa: BLE001
        logger.exception("failed to finalize reflection run %s; it stays RUNNING", handle.run_id)


async def build_lesson(client: AsyncAnthropic, material: ReflectionMaterial) -> LessonWithRun:
    """Makes the traced model call and returns the parsed Lesson together
    with the id of the Run that produced it (LessonWithRun).

    Owns its own Run end-to-end -- start_run here, end_run on every exit
    path -- so a caller never needs to know the run exists to get correct
    tracing. Raises ReflectionFailed on every failure; the run is always
    ended before this function returns or raises.
    """
    try:
        handle = start_run(RunTrigger.REFLECTION, conversation_id=material.conversation_id)
    except Exception as exc:  # noqa: BLE001
        raise ReflectionFailed(f"could not start a reflection run: {exc}") from exc

    try:
        async with span(SpanKind.LLM, "messages.parse") as recorder:
            try:
                response = await client.messages.parse(
                    model=_MODEL,
                    max_tokens=_MAX_TOKENS,
                    system=_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": material.content}],
                    output_format=Lesson,
                )
            except ValidationError as exc:
                _end_run_quietly(handle, status=RunStatus.ERROR, error=f"schema violation: {exc}")
                raise ReflectionFailed(f"the model's lesson did not validate: {exc}") from exc
            except Exception as exc:  # noqa: BLE001
                _end_run_quietly(handle, status=RunStatus.ERROR, error=f"{type(exc).__name__}: {exc}")
                raise ReflectionFailed(f"{type(exc).__name__}: {exc}") from exc

            parsed = getattr(response, "parsed_output", None)
            if not isinstance(parsed, Lesson):
                _end_run_quietly(handle, status=RunStatus.ERROR, error="no parsed lesson in the response")
                raise ReflectionFailed("the model returned no parsed lesson")

            usage = response.usage
            recorder.record_usage(
                model=response.model,
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
            )
    except ReflectionFailed:
        raise

    _end_run_quietly(handle, status=RunStatus.OK)
    return LessonWithRun(lesson=parsed, run_id=handle.run_id)


# Module-level singleton, same pattern and rationale as app/chat/router.py's
# and app/admin/dossier.py's identical _get_client: constructing a fresh
# AsyncAnthropic (and its underlying httpx connection pool) on every call
# accumulates unclosed pools under sustained ticket-resolution volume, since
# nothing ever calls its aclose() and Python GC does not deterministically
# close an async httpx transport. A test can still stub this out entirely
# via monkeypatch.setattr(reflect_module, "_get_client", ...).
_anthropic_client: object | None = None


def _get_client() -> AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        from app.config import get_settings
        _anthropic_client = AsyncAnthropic(api_key=get_settings().anthropic_api_key)
    return _anthropic_client


async def reflect(ticket_id: uuid.UUID) -> None:
    """The module's one public entrypoint (design spec 4.1). Opens its own
    session -- never the caller's, which by the time this runs (scheduled
    via BackgroundTasks from the resolve endpoint) no longer exists.

    Never raises. Nobody is waiting on a reflection: the resolve response
    already went out before this function is even called. A failure here
    is logged and the run it started (if it got that far) is marked ERROR;
    it never affects the ticket, which is already resolved and correct
    regardless of what reflection does (design decision D5).
    """
    from app.db.session import get_sessionmaker
    from app.learning import writer

    Session = get_sessionmaker()
    try:
        with Session() as db:
            ticket = db.get(Ticket, ticket_id)
            if ticket is None:
                logger.warning("reflect() called for a ticket that no longer exists: %s", ticket_id)
                return

            material = gather_material(db, ticket)
            client = _get_client()

            try:
                result = await build_lesson(client, material)
            except ReflectionFailed as exc:
                logger.warning("reflection failed for ticket TCK-%06d: %s", ticket.ticket_number, exc)
                return
            except Exception:  # noqa: BLE001 -- see docstring: this must never propagate
                logger.exception("reflection raised an unexpected error for ticket TCK-%06d", ticket.ticket_number)
                return

            if not result.lesson.should_record:
                logger.info("reflection did not record a lesson for ticket TCK-%06d", ticket.ticket_number)
                return

            try:
                db_lesson = await writer.create_lesson(db, ticket=ticket, lesson=result.lesson, run_id=result.run_id)
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
                logger.exception("failed to write the lesson for ticket TCK-%06d", ticket.ticket_number)
                return

            logger.info("recorded lesson %s for ticket TCK-%06d", db_lesson.id, ticket.ticket_number)
    except Exception:  # noqa: BLE001 -- see docstring: this must NEVER propagate.
        # Catches everything the inner try blocks don't: db.get(), gather_material(),
        # and _get_client() (a misconfigured ANTHROPIC_API_KEY raises here) all run
        # unguarded above -- a reviewed gap in the original Step 7 code, where only
        # build_lesson/create_lesson were wrapped.
        logger.exception("reflect() raised an unexpected error for ticket %s", ticket_id)
