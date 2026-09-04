"""Unit tests for app.learning.writer's pure rendering and file-write
functions. upsert_embedding and create_lesson are tested in Task 2's file
once the Lesson model and a fake RagBackend exist."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.learning.writer import render_markdown, slugify, write_lesson_file


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
