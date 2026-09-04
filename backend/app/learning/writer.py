"""Turns a parsed Lesson into a durable, retrievable one: a markdown file
on disk (written once, at creation -- see design decision D6), a `lessons`
row, and a Chroma embedding kept in sync on every subsequent edit or
archive/unarchive (design decision D7 -- one upsert path for all three).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import Lesson as DbLesson, LessonConfidence, RunStatus, RunTrigger, Ticket
from app.rag.backend import get_rag_backend
from app.tracing.context import get_current_run
from app.tracing.spans import end_run, start_run

logger = logging.getLogger(__name__)

# Repo root is three parents up from this file: backend/app/learning/writer.py
# -> backend/app/learning -> backend/app -> backend -> repo root.
KNOWLEDGE_LESSONS_DIR = Path(__file__).resolve().parents[3] / "knowledge" / "lessons"

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    slug = _SLUG_STRIP_RE.sub("-", text.lower()).strip("-")
    return slug


def render_markdown(lesson, *, ticket_number: int, created_at: datetime) -> str:
    """`lesson` is a Lesson (app.learning.reflect) but only its attributes
    are used, so writer.py stays free of a reflect.py import -- the two
    modules would otherwise import each other.

    `lesson.confidence` is a plain string on the pydantic Lesson (its real
    caller, create_lesson) but an enum member with a `.value` on the
    _FakeLesson used by this module's own unit tests -- getattr's fallback
    accepts either without writer.py importing reflect.Lesson to type-check
    against it."""
    applies_to = ", ".join(lesson.applies_to)
    confidence = getattr(lesson.confidence, "value", lesson.confidence)
    return (
        "---\n"
        f"title: {lesson.title}\n"
        f"category: {lesson.category}\n"
        f"confidence: {confidence}\n"
        f"applies_to: [{applies_to}]\n"
        f"ticket: TCK-{ticket_number:06d}\n"
        f"created_at: {created_at.isoformat()}\n"
        "---\n\n"
        "## Situation\n\n"
        f"{lesson.situation}\n\n"
        "## What worked\n\n"
        f"{lesson.what_worked}\n\n"
        "## What to do differently\n\n"
        f"{lesson.what_to_do_differently}\n"
    )


def write_lesson_file(*, content_md: str, ticket_number: int, title: str, created_at: datetime) -> str:
    KNOWLEDGE_LESSONS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{created_at:%Y-%m-%d}-TCK-{ticket_number:06d}-{slugify(title)}.md"
    path = KNOWLEDGE_LESSONS_DIR / filename
    path.write_text(content_md, encoding="utf-8")
    return str(path)


def _end_run_quietly(handle, *, status: RunStatus, error: str | None = None) -> None:
    """Same rationale as build_dossier's/build_lesson's identical helper:
    tracing is observability, not the product, and a failure finalizing the
    Run must never turn an embed that already succeeded -- or already
    failed for its own, already-reported reason -- into a second, different
    failure."""
    try:
        end_run(handle, status=status, error=error)
    except Exception:  # noqa: BLE001
        logger.exception("failed to finalize embed run %s; it stays RUNNING", handle.run_id)


async def _do_upsert(lesson_row: DbLesson) -> None:
    backend = get_rag_backend()
    await backend.upsert(
        "lessons",
        ids=[str(lesson_row.id)],
        documents=[lesson_row.content_md],
        metadatas=[{
            "lesson_id": str(lesson_row.id),
            "title": lesson_row.title,
            "category": lesson_row.category,
            "confidence": lesson_row.confidence.value,
            "applies_to": ", ".join(lesson_row.applies_to),
            "status": lesson_row.status.value,
        }],
    )


async def upsert_embedding(lesson_row: DbLesson, *, trigger: RunTrigger = RunTrigger.LESSON_EDIT) -> None:
    """Pushes the lesson's CURRENT full state to Chroma -- used identically
    for create, content edits, and archive/unarchive (design decision D7).
    There is no separate delete path: an archived lesson stays in Chroma
    with status=archived in its metadata, and search_lessons_handler's
    where={"status": "active"} filter is what actually excludes it.

    This is the single chokepoint for every real caller (create_lesson
    below, and admin/router.py's admin_patch_lesson/admin_archive_lesson),
    so it -- not either caller -- is where the tracing requirement below is
    handled once.

    The real backend (McpChromaBackend) wraps every Chroma call in a
    tracing span, and span() hard-requires an active Run. create_lesson's
    caller (reflect()) has none by the time it calls this: build_lesson
    already ended its own REFLECTION run before returning. Re-entering that
    already-ended run would silently corrupt tracing (end_run has already
    flushed its pending-spans buffer and finalized its rollup; a span
    opened against it afterward hits the "run already ended" branch and
    vanishes without error) -- so if there's no run active, this call owns
    a fresh one of its own rather than trying to resurrect the caller's.
    `trigger` lets callers pick what that fresh run is labeled as; when an
    ambient run already exists (e.g. a future caller running inside one),
    this joins it instead and `trigger` is unused.

    Deliberately does NOT accept a conversation_id to tag this fresh run
    with: an earlier version of this fix threaded create_lesson's
    ticket.conversation_id through to start_run so the run could be swept
    by the same conversation_id filter test_learning_reflect.py's
    _cleanup_committed_ticket already uses. That works for reflect()'s real
    flow (create_lesson runs against its own hard-committed session, which
    closes before the test's cleanup runs) but deadlocks a test that calls
    create_lesson directly against the test's own long-lived db_session:
    inserting the Lesson row takes a FOR KEY SHARE lock on the referenced
    tickets row that is held for the rest of the test (db_session never
    truly commits until fixture teardown), and a real DELETE FROM tickets
    on a separate connection then blocks forever waiting for that
    never-ending transaction -- reproduced directly against the real
    Postgres instance. Not worth the coupling for an observability nicety;
    the cleanup gap is fixed test-side instead (see
    test_learning_reflect.py's _cleanup_committed_ticket)."""
    if get_current_run() is not None:
        await _do_upsert(lesson_row)
        return

    handle = start_run(trigger)
    try:
        await _do_upsert(lesson_row)
    except Exception as exc:  # noqa: BLE001
        _end_run_quietly(handle, status=RunStatus.ERROR, error=f"{type(exc).__name__}: {exc}")
        raise
    else:
        _end_run_quietly(handle, status=RunStatus.OK)


async def create_lesson(db: Session, *, ticket: Ticket, lesson, run_id) -> DbLesson:
    """Writes the file once (design decision D6), inserts the row, embeds
    it, and stamps embedded_at only on a successful embed -- a NULL
    embedded_at is an honest "not yet retrievable" signal, not a bug, if
    the embed step below ever fails (see the module docstring's design D7
    for the parallel decision on the admin edit path).

    upsert_embedding is called with trigger=RunTrigger.REFLECTION: by the
    time this runs, build_lesson (reflect()'s only other tracing caller)
    has already ended its own REFLECTION run, so there is no ambient run
    for this call to join -- it owns a fresh one, and REFLECTION is the
    label that best describes what's happening (recording a lesson learned
    from a ticket), same as the run that produced the lesson content
    itself.

    If anything from the DB insert through the embed fails, the file
    written just above is removed before the exception propagates -- a
    lesson row that never made it (this function raises; the caller rolls
    back) must not leave an orphaned .md file with nothing in the DB or
    Chroma pointing at it. Design decision D6 says the file is written
    once at creation and untouched by later edits; it does not say a
    creation that got rolled back should keep its file."""
    created_at = datetime.now(timezone.utc)
    content_md = render_markdown(lesson, ticket_number=ticket.ticket_number, created_at=created_at)
    file_path = write_lesson_file(
        content_md=content_md, ticket_number=ticket.ticket_number,
        title=lesson.title, created_at=created_at,
    )

    try:
        row = DbLesson(
            ticket_id=ticket.id, title=lesson.title, category=lesson.category,
            content_md=content_md, file_path=file_path, applies_to=lesson.applies_to,
            # lesson.confidence is a plain str on the pydantic Lesson
            # (Literal["low","medium","high"]); SQLAlchemy's SAEnum accepts
            # the raw string when binding it for the INSERT, but the Python
            # attribute on `row` stays whatever was assigned -- it is never
            # coerced back into a LessonConfidence member -- so
            # upsert_embedding's `row.confidence.value` below would raise
            # AttributeError on a plain str. Converting explicitly here keeps
            # the in-memory row's type consistent with the column's Mapped type.
            confidence=LessonConfidence(lesson.confidence), created_by_run_id=run_id,
        )
        db.add(row)
        db.flush()  # assigns row.id, needed by upsert_embedding, without committing

        await upsert_embedding(row, trigger=RunTrigger.REFLECTION)
    except Exception:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise

    row.embedded_at = datetime.now(timezone.utc)
    return row
