"""Turns a parsed Lesson into a durable, retrievable one: a markdown file
on disk (written once, at creation -- see design decision D6), a `lessons`
row, and a Chroma embedding kept in sync on every subsequent edit or
archive/unarchive (design decision D7 -- one upsert path for all three).
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

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
    modules would otherwise import each other."""
    applies_to = ", ".join(lesson.applies_to)
    return (
        "---\n"
        f"title: {lesson.title}\n"
        f"category: {lesson.category}\n"
        f"confidence: {lesson.confidence.value}\n"
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
