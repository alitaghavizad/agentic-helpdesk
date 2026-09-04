"""Unit tests for app.learning.writer's pure rendering and file-write
functions, plus upsert_embedding and create_lesson."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.learning.writer import render_markdown, slugify, write_lesson_file


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


class _FakeLesson:
    def __init__(self, **overrides):
        defaults = dict(
            title="VPN certificate renewal blocks login",
            category="Network and VPN Support",
            confidence_value="high",
            applies_to=["vpn", "certificates"],
            situation="A user's VPN client rejected a renewed certificate.",
            what_worked="Re-importing the root CA bundle fixed it immediately.",
            what_to_do_differently="Check certificate chain validity before escalating.",
        )
        defaults.update(overrides)
        self.title = defaults["title"]
        self.category = defaults["category"]
        self.applies_to = defaults["applies_to"]
        self.situation = defaults["situation"]
        self.what_worked = defaults["what_worked"]
        self.what_to_do_differently = defaults["what_to_do_differently"]

        class _Confidence:
            def __init__(self, value):
                self.value = value

        self.confidence = _Confidence(defaults["confidence_value"])


def test_slugify_lowercases_and_hyphenates():
    assert slugify("VPN Certificate Renewal Blocks Login!") == "vpn-certificate-renewal-blocks-login"


def test_slugify_collapses_repeated_separators():
    assert slugify("a   b---c") == "a-b-c"


def test_slugify_strips_leading_and_trailing_hyphens():
    assert slugify("  --edge case--  ") == "edge-case"


def test_render_markdown_has_yaml_frontmatter_with_all_fields():
    lesson = _FakeLesson()
    created_at = datetime(2026, 9, 4, 14, 22, 1, tzinfo=timezone.utc)

    doc = render_markdown(lesson, ticket_number=123, created_at=created_at)

    assert doc.startswith("---\n")
    assert "title: VPN certificate renewal blocks login" in doc
    assert "category: Network and VPN Support" in doc
    assert "confidence: high" in doc
    assert "applies_to: [vpn, certificates]" in doc
    assert "ticket: TCK-000123" in doc
    assert "created_at: 2026-09-04T14:22:01+00:00" in doc


def test_render_markdown_has_the_three_body_sections_in_order():
    lesson = _FakeLesson()
    doc = render_markdown(lesson, ticket_number=123, created_at=datetime.now(timezone.utc))

    situation_idx = doc.index("## Situation")
    worked_idx = doc.index("## What worked")
    differently_idx = doc.index("## What to do differently")
    assert situation_idx < worked_idx < differently_idx
    assert lesson.situation in doc
    assert lesson.what_worked in doc
    assert lesson.what_to_do_differently in doc


def test_write_lesson_file_returns_the_documented_path_format(tmp_path, monkeypatch):
    # write_lesson_file resolves paths relative to the repo root via a
    # KNOWLEDGE_LESSONS_DIR constant, patched here so the test writes into
    # a temp directory instead of the real knowledge/ tree.
    import app.learning.writer as writer_module

    lessons_dir = tmp_path / "knowledge" / "lessons"
    monkeypatch.setattr(writer_module, "KNOWLEDGE_LESSONS_DIR", lessons_dir)

    created_at = datetime(2026, 9, 4, 14, 22, 1, tzinfo=timezone.utc)
    path = write_lesson_file(
        content_md="---\ntitle: x\n---\nbody",
        ticket_number=123,
        title="VPN certificate renewal blocks login",
        created_at=created_at,
    )

    assert path == str(lessons_dir / "2026-09-04-TCK-000123-vpn-certificate-renewal-blocks-login.md")
    assert (lessons_dir / "2026-09-04-TCK-000123-vpn-certificate-renewal-blocks-login.md").read_text() == "---\ntitle: x\n---\nbody"


def test_write_lesson_file_creates_the_directory_if_missing(tmp_path, monkeypatch):
    import app.learning.writer as writer_module

    lessons_dir = tmp_path / "does" / "not" / "exist" / "yet"
    monkeypatch.setattr(writer_module, "KNOWLEDGE_LESSONS_DIR", lessons_dir)

    write_lesson_file(
        content_md="x", ticket_number=1, title="t",
        created_at=datetime.now(timezone.utc),
    )

    assert lessons_dir.exists()


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


class TestUpsertEmbedding:
    async def test_upserts_content_and_metadata_including_status(self, db_session, monkeypatch):
        from app.db.models import Lesson as DbLesson, LessonConfidence, LessonStatus, Run, RunStatus, RunTrigger
        import app.learning.writer as writer_module

        fake_backend = _FakeRagBackend()
        monkeypatch.setattr(writer_module, "get_rag_backend", lambda: fake_backend)

        # created_by_run_id is a real NOT NULL FK to runs.id -- a bare
        # uuid.uuid4() with no backing row trips ForeignKeyViolation on
        # flush, so a Run is flushed first (same transaction, so it's
        # visible to the Lesson insert without needing a hard commit).
        run = Run(trigger=RunTrigger.REFLECTION, status=RunStatus.OK)
        db_session.add(run)
        db_session.flush()

        lesson = DbLesson(
            title="t", category="c", content_md="body", file_path="/x/y.md",
            applies_to=["a", "b"], confidence=LessonConfidence.HIGH,
            status=LessonStatus.ACTIVE, created_by_run_id=run.id,
        )
        db_session.add(lesson)
        db_session.flush()  # assigns lesson.id without committing

        await writer_module.upsert_embedding(lesson)

        assert len(fake_backend.upserts) == 1
        call = fake_backend.upserts[0]
        assert call["collection"] == "lessons"
        assert call["ids"] == [str(lesson.id)]
        assert call["documents"] == ["body"]
        assert call["metadatas"][0]["status"] == "active"
        assert call["metadatas"][0]["applies_to"] == "a, b"
        assert call["metadatas"][0]["lesson_id"] == str(lesson.id)


class TestCreateLesson:
    async def test_writes_file_inserts_row_and_embeds(self, db_session, make_ticket, monkeypatch, tmp_path):
        from app.db.models import Lesson as DbLesson, Run, RunStatus, RunTrigger
        from app.learning.reflect import Lesson
        import app.learning.writer as writer_module

        monkeypatch.setattr(writer_module, "KNOWLEDGE_LESSONS_DIR", tmp_path)
        fake_backend = _FakeRagBackend()
        monkeypatch.setattr(writer_module, "get_rag_backend", lambda: fake_backend)

        ticket = make_ticket()
        # created_by_run_id is a real NOT NULL FK to runs.id -- see the
        # identical note in TestUpsertEmbedding above.
        run = Run(trigger=RunTrigger.REFLECTION, status=RunStatus.OK)
        db_session.add(run)
        db_session.flush()
        run_id = run.id
        lesson = Lesson(**_valid_lesson_kwargs())

        db_lesson = await writer_module.create_lesson(db_session, ticket=ticket, lesson=lesson, run_id=run_id)
        db_session.commit()

        assert db_lesson.id is not None
        assert db_lesson.embedded_at is not None
        assert db_lesson.file_path.startswith(str(tmp_path))
        assert Path(db_lesson.file_path).exists()
        assert db_lesson.ticket_id == ticket.id
        assert db_lesson.created_by_run_id == run_id
        assert len(fake_backend.upserts) == 1

        stored = db_session.query(DbLesson).filter(DbLesson.id == db_lesson.id).one()
        assert stored.content_md == db_lesson.content_md
