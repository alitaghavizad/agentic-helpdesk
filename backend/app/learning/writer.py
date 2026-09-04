"""Turns a parsed Lesson into a durable, retrievable one: a markdown file
on disk (written once, at creation -- see design decision D6), a `lessons`
row, and a Chroma embedding kept in sync on every subsequent edit or
archive/unarchive (design decision D7 -- one upsert path for all three).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import Lesson as DbLesson, LessonConfidence, Ticket
from app.rag.backend import get_rag_backend

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


async def upsert_embedding(lesson_row: DbLesson) -> None:
    """Pushes the lesson's CURRENT full state to Chroma -- used identically
    for create, content edits, and archive/unarchive (design decision D7).
    There is no separate delete path: an archived lesson stays in Chroma
    with status=archived in its metadata, and search_lessons_handler's
    where={"status": "active"} filter is what actually excludes it."""
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


async def create_lesson(db: Session, *, ticket: Ticket, lesson, run_id) -> DbLesson:
    """Writes the file once (design decision D6), inserts the row, embeds
    it, and stamps embedded_at only on a successful embed -- a NULL
    embedded_at is an honest "not yet retrievable" signal, not a bug, if
    the embed step below ever fails (see the module docstring's design D7
    for the parallel decision on the admin edit path)."""
    created_at = datetime.now(timezone.utc)
    content_md = render_markdown(lesson, ticket_number=ticket.ticket_number, created_at=created_at)
    file_path = write_lesson_file(
        content_md=content_md, ticket_number=ticket.ticket_number,
        title=lesson.title, created_at=created_at,
    )

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

    await upsert_embedding(row)
    row.embedded_at = datetime.now(timezone.utc)
    return row
