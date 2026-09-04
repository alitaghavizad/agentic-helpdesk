# Phase 9 — Learning Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On ticket resolution, run a background reflection call that decides whether the resolution taught a durable lesson; if so, write it to disk, insert it, and embed it, so `search_lessons` can retrieve it — closing the one loop the agent has never been able to complete.

**Architecture:** Two new modules, `app/learning/reflect.py` (the traced model call, `should_record` gate) and `app/learning/writer.py` (markdown rendering, file write, DB insert, Chroma upsert). Triggered via `BackgroundTasks` from `POST /tickets/{id}/resolve`, so resolving a ticket never waits on a model call. The existing admin lesson-edit/archive endpoints gain the missing re-embed step, and `search_lessons` gains the status filter that makes archiving actually work.

**Tech Stack:** Python/FastAPI/SQLAlchemy backend (existing). `anthropic.AsyncAnthropic`, `client.messages.parse` with `output_format=`. Chroma via the existing `RagBackend` protocol. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-04-learning-loop-design.md`

## Global Constraints

- **`reflect(ticket_id: uuid.UUID) -> None` is the module's one public entrypoint** (parent spec §4.1: `learning | lessons | reflect(ticket_id)`). It is `async def` and opens its own DB session via `get_sessionmaker()()` — never accept a caller's session, because `BackgroundTasks` runs after the request's session is gone.
- **`RunTrigger.REFLECTION` is used for every reflection call, whether or not it produces a lesson.** `should_record: false` means "write nothing," not "this run doesn't get traced."
- **A failed reflection never raises out of `reflect()`.** Catch everything at the top level, end the run `ERROR`, log, return. Nobody is waiting on this call; the resolve response already went out.
- **The on-disk `.md` file is written once, at creation.** Admin edits touch `content_md` (DB) and the Chroma embedding, never the file.
- **`writer.upsert_embedding(lesson)` always pushes the lesson's current full state** (content + `status` in metadata) to Chroma — used identically for create, edit, and archive/unarchive. No separate delete path.
- **The two admin lesson-mutation endpoints attempt the embed *before* `db.commit()`.** If the embed raises, roll back and return 503 — the edit is idempotent, so a retry is always safe, and the DB and Chroma must never be allowed to disagree about a lesson's content or status.
- **`search_lessons_handler` queries `where={"status": "active"}`**, not `where={}`. This is the one line that makes archiving actually remove a lesson from what the agent retrieves.
- **Reflection's model call mirrors `build_dossier`'s exact shape** (`model`, `max_tokens`, `system`, `messages`, `output_format=`) — no `effort`/`output_config`/`thinking` kwargs. Those only appear on `run_turn`'s beta streaming call (`agent/loop.py:42`) in this codebase; `messages.parse` never uses them anywhere, including in the dossier, which nominally wants "high" effort per spec §8.1 but never sets it. Don't invent an untested kwarg here.
- Backend tests: `cd backend && uv run pytest tests/<file> -v`. Full suite: `cd backend && uv run python tasks.py test` (~6 min). Never background these — pass an explicit `timeout` (max 600000ms) and run in the foreground.
- Postgres (`postgres18`) and Chroma must be running in Docker for tests. `backend/.env` already has `ANTHROPIC_API_KEY`.
- `make` is not installed; use `uv run python tasks.py <task>`.
- Commit after every task.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/learning/__init__.py` (create, empty) | Makes `app.learning` a package |
| `backend/app/learning/reflect.py` (create) | `ReflectionMaterial` dataclass, `gather_material`, `build_lesson`, `reflect(ticket_id)` — the traced model call and its `should_record` gate |
| `backend/app/learning/writer.py` (create) | `slugify`, `render_markdown`, `write_lesson_file`, `upsert_embedding`, `create_lesson` — everything that turns a parsed `Lesson` into a durable, retrievable one |
| `backend/app/tickets/router.py` (modify) | `resolve_ticket_endpoint` gains a `background_tasks: BackgroundTasks` param and schedules `reflect` |
| `backend/app/admin/router.py` (modify) | `admin_patch_lesson`, `admin_archive_lesson` become `async def` and re-embed before commit |
| `backend/app/agent/tools/knowledge.py` (modify) | `search_lessons_handler`'s query gains `where={"status": "active"}` |
| `backend/tests/test_learning_writer.py` (create) | Unit tests for `writer.py` |
| `backend/tests/test_learning_reflect.py` (create) | Unit tests for `reflect.py` against a stubbed async client |
| `backend/tests/test_tickets_resolve_schedules_reflection.py` (create) | Confirms the resolve endpoint schedules the background task |
| `backend/tests/test_admin_lessons_reembed.py` (create) | Confirms patch/archive re-embed and roll back on failure |
| `backend/tests/test_agent_tools_knowledge.py` (modify) | Extend the existing `search_lessons` tests with the status filter |
| `backend/tests/test_learning_live.py` (create) | The one live, real-API gate test (`live_reflection` marker) |
| `backend/pyproject.toml` (modify) | Register the `live_reflection` marker |

---

## Task 0: Verify the async SDK contract, first

Nothing else in this plan should be built until this is confirmed. `build_dossier` proves the **sync** `client.messages.parse(output_format=X)` → `.parsed_output` contract against the installed `anthropic` package; this phase needs the **async** client to have the identical contract, and that has never been exercised anywhere in this codebase.

**Files:**
- Test: `backend/tests/test_learning_reflect.py` (this task creates the file; later tasks add to it)

**Interfaces:**
- Produces: confirmation that `anthropic.AsyncAnthropic().messages.parse(...)` is awaitable and returns an object with `.parsed_output`.

- [ ] **Step 1: Write a standalone script proving the contract against the real SDK (not a mock)**

Create a throwaway check — run this directly, it is not a pytest test:

```python
# Run with: cd backend && uv run python -c "..."
import asyncio
import inspect
import anthropic

client = anthropic.AsyncAnthropic(api_key="sk-not-a-real-key-just-checking-the-shape")
print("messages.parse is coroutine function:", inspect.iscoroutinefunction(client.messages.parse))
```

Run: `cd backend && uv run python -c "import asyncio, inspect, anthropic; client = anthropic.AsyncAnthropic(api_key='x'); print(inspect.iscoroutinefunction(client.messages.parse))"`

Expected: `True`. If `False` or the attribute doesn't exist, **STOP** — report this to the user before writing anything else, because the whole async design in the spec depends on this. Do not silently fall back to a sync client without flagging it; that would undo the design's D2 decision (proper cost tracking via `span()`, which is async-only).

- [ ] **Step 2: Create the test file with its imports, ready for Task 3 to add real tests**

```python
"""Unit tests for app.learning.reflect — the traced model call and its
should_record gate. Every test here stubs the Anthropic client; the ONLY
test that proves a real model can fill Lesson is test_learning_live.py.
"""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.db.models import RunStatus, RunTrigger
```

- [ ] **Step 3: Commit the confirmation**

```bash
git add backend/tests/test_learning_reflect.py
git commit -m "Confirm AsyncAnthropic.messages.parse is awaitable before building on it"
```

---

## Task 1: `writer.py` — markdown rendering and the file write

**Files:**
- Create: `backend/app/learning/__init__.py` (empty), `backend/app/learning/writer.py`
- Test: `backend/tests/test_learning_writer.py`

**Interfaces:**
- Consumes: nothing from other tasks yet — this is pure functions over data the caller already has.
- Produces:
  - `slugify(text: str) -> str`
  - `render_markdown(lesson, ticket_number: int, created_at: datetime) -> str` where `lesson` is any object with `.title`, `.category`, `.confidence` (has `.value`), `.applies_to`, `.situation`, `.what_worked`, `.what_to_do_differently` — the `Lesson` pydantic model from `reflect.py` (Task 2), but this function only needs those attributes so it can be tested standalone first.
  - `write_lesson_file(content_md: str, ticket_number: int, title: str, created_at: datetime) -> str` — returns the file path it wrote, relative to the repo root, e.g. `knowledge/lessons/2026-09-04-TCK-000123-vpn-cert-renewal.md`.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run and confirm every test fails**

Run: `cd backend && uv run pytest tests/test_learning_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.learning'`.

- [ ] **Step 3: Create the package and implement**

Create `backend/app/learning/__init__.py` — empty file.

Create `backend/app/learning/writer.py`:

```python
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
```

- [ ] **Step 4: Run and confirm every test passes**

Run: `cd backend && uv run pytest tests/test_learning_writer.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/learning/__init__.py backend/app/learning/writer.py backend/tests/test_learning_writer.py
git commit -m "Add the lesson markdown renderer and file writer"
```

---

## Task 2: `reflect.py` — the `Lesson` model, material gathering, and the traced call

**Files:**
- Create: `backend/app/learning/reflect.py`
- Modify: `backend/tests/test_learning_reflect.py` (created in Task 0)

**Interfaces:**
- Consumes: nothing yet from `writer.py` — this task's `build_lesson` only returns a parsed `Lesson`; wiring it to `writer.create_lesson` is Task 3.
- Produces:
  - `class Lesson(BaseModel)` — exactly the five-plus-three fields from parent spec §13: `should_record: bool`, `title: str`, `category: str`, `situation: str`, `what_worked: str`, `what_to_do_differently: str`, `applies_to: list[str]`, `confidence: Literal["low", "medium", "high"]`.
  - `@dataclass class ReflectionMaterial: conversation_id: uuid.UUID | None; content: str` — the prompt text plus the conversation id `start_run` needs.
  - `gather_material(db: Session, ticket) -> ReflectionMaterial`
  - `class ReflectionFailed(RuntimeError)` — mirrors `DossierFailed`'s role, raised by `build_lesson` on every failure path.
  - `@dataclass class LessonWithRun: lesson: Lesson; run_id: uuid.UUID` — carries the parsed `Lesson` together with the id of the `Run` that produced it, so a caller writing the lesson to disk can set `Lesson.created_by_run_id` (a NOT NULL foreign key) to a run that actually exists, rather than to a freshly generated, untracked UUID.
  - `async def build_lesson(client, material: ReflectionMaterial) -> LessonWithRun` — raises `ReflectionFailed` on any failure; ends its own `Run` as `ERROR` before raising, `OK` before returning. **Callers do not call `start_run`/`end_run` themselves** — `build_lesson` owns the whole traced call, same responsibility split as `build_dossier`.

- [ ] **Step 1: Write the failing tests, appending to `test_learning_reflect.py`**

```python
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
    return ReflectionMaterial(conversation_id=uuid.uuid4(), content="the prompt content")


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
    @pytest.mark.asyncio
    async def test_returns_the_parsed_lesson_and_its_run_id_on_success(self, cleanup_run):
        from app.learning.reflect import build_lesson

        client = _FakeAsyncClient(result=_valid_lesson())
        result = await build_lesson(client, _material())

        assert result.lesson.should_record is True
        assert result.lesson.title == "VPN certificate renewal blocks login"
        assert isinstance(result.run_id, uuid.UUID)
        assert client.messages.calls[0]["output_format"].__name__ == "Lesson"
        cleanup_run(result.run_id)

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_a_should_record_false_lesson_still_succeeds(self, cleanup_run):
        from app.learning.reflect import build_lesson

        client = _FakeAsyncClient(result=_valid_lesson(should_record=False))
        result = await build_lesson(client, _material())

        assert result.lesson.should_record is False
        cleanup_run(result.run_id)

    @pytest.mark.asyncio
    async def test_a_validation_error_ends_the_run_as_error_and_raises_reflection_failed(self, cleanup_run, db_session):
        from app.db.models import Run
        from app.learning.reflect import ReflectionFailed, build_lesson

        client = _FakeAsyncClient(raises=ValidationError.from_exception_data("Lesson", []))

        with pytest.raises(ReflectionFailed):
            await build_lesson(client, _material())

        run = db_session.query(Run).filter(Run.trigger == RunTrigger.REFLECTION).order_by(Run.started_at.desc()).first()
        assert run.status == RunStatus.ERROR
        cleanup_run(run.id)

    @pytest.mark.asyncio
    async def test_a_response_with_no_parsed_output_is_an_error(self, cleanup_run, db_session):
        from app.db.models import Run
        from app.learning.reflect import ReflectionFailed, build_lesson

        client = _FakeAsyncClient(result=None)

        with pytest.raises(ReflectionFailed):
            await build_lesson(client, _material())

        run = db_session.query(Run).filter(Run.trigger == RunTrigger.REFLECTION).order_by(Run.started_at.desc()).first()
        assert run.status == RunStatus.ERROR
        cleanup_run(run.id)

    @pytest.mark.asyncio
    async def test_an_unexpected_exception_is_also_reflection_failed(self, cleanup_run, db_session):
        from app.db.models import Run
        from app.learning.reflect import ReflectionFailed, build_lesson

        client = _FakeAsyncClient(raises=RuntimeError("upstream exploded"))

        with pytest.raises(ReflectionFailed, match="upstream exploded"):
            await build_lesson(client, _material())

        run = db_session.query(Run).filter(Run.trigger == RunTrigger.REFLECTION).order_by(Run.started_at.desc()).first()
        assert run.status == RunStatus.ERROR
        cleanup_run(run.id)
```

This test file needs `pytest-asyncio`. Check first: `grep pytest-asyncio backend/pyproject.toml`. If it's already a dependency (the async agent-loop tests must use *something* to run `async def` tests), use whatever marker/config those tests use — check `backend/tests/test_agent_loop.py`'s first few lines and `backend/pyproject.toml`'s `[tool.pytest.ini_options]` for `asyncio_mode`. If `asyncio_mode = "auto"` is already set, delete the `@pytest.mark.asyncio` decorators above — they'd be redundant and some configurations warn on redundant markers. Match whatever the existing async tests in this codebase actually do; do not assume.

- [ ] **Step 2: Run and confirm every new test fails**

Run: `cd backend && uv run pytest tests/test_learning_reflect.py -v`
Expected: FAIL — `ImportError: cannot import name 'gather_material' from 'app.learning.reflect'` (module doesn't exist yet beyond the empty scaffold from Task 0).

- [ ] **Step 3: Implement `reflect.py` up through `build_lesson`**

```python
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

from app.db.models import Message, MessageRole, RunStatus, RunTrigger, Ticket
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
    fields, the routing decision that assigned it, and the conversation
    that produced it -- not the full span tree, which is the dossier's
    job for a different reader (design decision D9)."""
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
```

**A note on the `async with span(...)` placement above:** `record_usage` is called *inside* the `span` block (before it closes), which is what actually attaches the usage to the span that gets persisted — this mirrors `agent/loop.py:97-108`'s exact structure line for line. Do not move `record_usage` outside the `async with` block; the recorder object is only meaningful while its span is open.

If `_FakeAsyncMessages.parse` raising an exception from *inside* an `async with span(...)` block causes any issue with span cleanup (e.g. the span's own `__aexit__` needing to run even when the inner code raises), that is exactly what an `async with` block already guarantees — no special handling needed. If a test fails in a way that suggests otherwise, investigate `tracing/spans.py`'s `__aexit__` implementation before changing this task's structure.

- [ ] **Step 4: Run and confirm every test passes**

Run: `cd backend && uv run pytest tests/test_learning_reflect.py -v`
Expected: 7 passed (1 `TestGatherMaterial` test + 6 `TestBuildLesson` tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/learning/reflect.py backend/tests/test_learning_reflect.py
git commit -m "Add the Lesson model, material gathering, and the traced reflection call"
```

---

## Task 3: Wire `reflect()` end-to-end and `writer.create_lesson`

**Files:**
- Modify: `backend/app/learning/reflect.py` (add the `reflect(ticket_id)` entrypoint)
- Modify: `backend/app/learning/writer.py` (add `upsert_embedding`, `create_lesson`)
- Modify: `backend/tests/test_learning_reflect.py`, `backend/tests/test_learning_writer.py`

**Interfaces:**
- Consumes: `gather_material`, `build_lesson`, `Lesson`, `LessonWithRun` from Task 2; `render_markdown`, `write_lesson_file` from Task 1.
- Produces:
  - `async def upsert_embedding(lesson_row) -> None` in `writer.py` — takes a `Lesson` ORM row (`app.db.models.Lesson`, note the name collision with `reflect.Lesson` the pydantic model — they are different classes in different modules, never imported into the same namespace under the same name).
  - `async def create_lesson(db: Session, *, ticket: Ticket, lesson: Lesson, run_id: uuid.UUID) -> DbLesson` in `writer.py` — takes the parsed pydantic `Lesson` and the run id as two separate keyword arguments (not a `LessonWithRun`), so `writer.py` never needs to import anything from `reflect.py`; `reflect()` is the one place that unpacks a `LessonWithRun` before calling this.
  - `async def reflect(ticket_id: uuid.UUID) -> None` in `reflect.py` — the module's public entrypoint, opens its own session, never raises.

- [ ] **Step 1: Write the failing tests for `writer.upsert_embedding` and `create_lesson`, appending to `test_learning_writer.py`**

```python
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
        from app.db.models import Lesson as DbLesson, LessonConfidence, LessonStatus
        import app.learning.writer as writer_module

        fake_backend = _FakeRagBackend()
        monkeypatch.setattr(writer_module, "get_rag_backend", lambda: fake_backend)

        lesson = DbLesson(
            title="t", category="c", content_md="body", file_path="/x/y.md",
            applies_to=["a", "b"], confidence=LessonConfidence.HIGH,
            status=LessonStatus.ACTIVE, created_by_run_id=uuid.uuid4(),
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
        from app.db.models import Lesson as DbLesson
        from app.learning.reflect import Lesson
        import app.learning.writer as writer_module

        monkeypatch.setattr(writer_module, "KNOWLEDGE_LESSONS_DIR", tmp_path)
        fake_backend = _FakeRagBackend()
        monkeypatch.setattr(writer_module, "get_rag_backend", lambda: fake_backend)

        ticket = make_ticket()
        run_id = uuid.uuid4()
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
```

Add `from pathlib import Path` and `import uuid` to the top of `test_learning_writer.py` if not already present, and reuse the `_valid_lesson_kwargs` helper from `test_learning_reflect.py` — copy it into `test_learning_writer.py` too rather than importing across test files (test files in this codebase don't import from each other; each is self-contained, matching every existing test file's pattern of module-local helpers).

- [ ] **Step 2: Run and confirm these fail**

Run: `cd backend && uv run pytest tests/test_learning_writer.py -v`
Expected: FAIL — `AttributeError: module 'app.learning.writer' has no attribute 'upsert_embedding'`.

- [ ] **Step 3: Implement `upsert_embedding` and `create_lesson` in `writer.py`**

Add to `backend/app/learning/writer.py`:

```python
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import Lesson as DbLesson, Ticket
from app.rag.backend import get_rag_backend


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
        confidence=lesson.confidence, created_by_run_id=run_id,
    )
    db.add(row)
    db.flush()  # assigns row.id, needed by upsert_embedding, without committing

    await upsert_embedding(row)
    row.embedded_at = datetime.now(timezone.utc)
    return row
```

`lesson.confidence` here is a Python string (`Literal["low","medium","high"]` from the pydantic `Lesson`, not an enum), and `DbLesson.confidence` is a `LessonConfidence` enum column — check whether SQLAlchemy's `SAEnum` with `values_callable` accepts the raw string directly on assignment (it likely does, since `LessonConfidence(str, enum.Enum)` makes `"high" == LessonConfidence.HIGH` true and SQLAlchemy's enum type typically accepts either the enum member or its value). If assigning the raw string raises a `LookupError` or similar when `db.flush()` runs, wrap it explicitly: `confidence=LessonConfidence(lesson.confidence)`. Run the test and let it tell you which is needed rather than guessing.

- [ ] **Step 4: Run and confirm these pass**

Run: `cd backend && uv run pytest tests/test_learning_writer.py -v`
Expected: all passed (previous 6 plus the new ones).

- [ ] **Step 5: Write the failing test for `reflect()` itself, appending to `test_learning_reflect.py`**

```python
class TestReflect:
    async def test_records_a_lesson_when_should_record_is_true(self, make_ticket, monkeypatch, tmp_path):
        import app.learning.reflect as reflect_module
        import app.learning.writer as writer_module
        from app.db.models import Lesson as DbLesson
        from app.db.session import get_sessionmaker

        ticket = make_ticket()
        ticket_id = ticket.id

        monkeypatch.setattr(writer_module, "KNOWLEDGE_LESSONS_DIR", tmp_path)
        monkeypatch.setattr(writer_module, "get_rag_backend", lambda: _FakeRagBackend())
        monkeypatch.setattr(reflect_module, "_get_client", lambda: _FakeAsyncClient(result=_valid_lesson()))

        await reflect_module.reflect(ticket_id)

        Session = get_sessionmaker()
        with Session() as s:
            lesson = s.query(DbLesson).filter(DbLesson.ticket_id == ticket_id).one_or_none()
            assert lesson is not None
            assert lesson.embedded_at is not None
            s.query(DbLesson).filter(DbLesson.ticket_id == ticket_id).delete()
            s.commit()

    async def test_records_nothing_when_should_record_is_false(self, make_ticket, monkeypatch, cleanup_run):
        import app.learning.reflect as reflect_module
        from app.db.models import Lesson as DbLesson, Run, RunTrigger
        from app.db.session import get_sessionmaker

        ticket = make_ticket()
        ticket_id = ticket.id

        monkeypatch.setattr(reflect_module, "_get_client", lambda: _FakeAsyncClient(result=_valid_lesson(should_record=False)))

        await reflect_module.reflect(ticket_id)

        Session = get_sessionmaker()
        with Session() as s:
            assert s.query(DbLesson).filter(DbLesson.ticket_id == ticket_id).count() == 0
            run = s.query(Run).filter(Run.trigger == RunTrigger.REFLECTION).order_by(Run.started_at.desc()).first()
            assert run is not None
            cleanup_run(run.id)

    async def test_a_failed_reflection_never_raises(self, make_ticket, monkeypatch):
        import app.learning.reflect as reflect_module

        ticket = make_ticket()
        monkeypatch.setattr(reflect_module, "_get_client", lambda: _FakeAsyncClient(raises=RuntimeError("network exploded")))

        await reflect_module.reflect(ticket.id)  # must not raise
```

Note: `make_ticket` builds its `Conversation`/`Run`/`Task`/`Ticket` chain through `db_session`'s savepoint-based transaction, which rolls back at test teardown — but `reflect()` opens its OWN session via `get_sessionmaker()()`, a genuinely separate connection. Per this codebase's established, already-documented hazard (`tests/test_approvals_service.py`, `tests/test_admin_dossier_live.py`): a row that exists only inside `db_session`'s savepoint is invisible to a different connection under READ COMMITTED. **`make_ticket` must therefore be confirmed to hard-commit**, not just flush, before this task's tests can pass — check `conftest.py`'s `make_ticket` fixture; if it calls `db_session.commit()` internally (it does, per the earlier exploration: `db_session.commit()` appears three times in `_make`), the ticket really is visible to `reflect()`'s separate session, and no extra fixture work is needed here. If it is NOT hard-committing, that's a test-infrastructure blocker to flag rather than work around.

- [ ] **Step 6: Run and confirm these fail**

Run: `cd backend && uv run pytest tests/test_learning_reflect.py::TestReflect -v`
Expected: FAIL — `AttributeError: module 'app.learning.reflect' has no attribute 'reflect'`.

- [ ] **Step 7: Implement `reflect()` and `_get_client()` in `reflect.py`**

Append to `backend/app/learning/reflect.py`:

```python
def _get_client() -> AsyncAnthropic:
    from app.config import get_settings
    return AsyncAnthropic(api_key=get_settings().anthropic_api_key)


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
```

- [ ] **Step 8: Run and confirm all reflect.py and writer.py tests pass**

Run: `cd backend && uv run pytest tests/test_learning_reflect.py tests/test_learning_writer.py -v`
Expected: all passed.

- [ ] **Step 9: Commit**

```bash
git add backend/app/learning/reflect.py backend/app/learning/writer.py backend/tests/test_learning_reflect.py backend/tests/test_learning_writer.py
git commit -m "Wire reflect() end to end: gather material, decide, write, embed"
```

---

## Task 4: Trigger reflection from ticket resolution

**Files:**
- Modify: `backend/app/tickets/router.py`
- Test: `backend/tests/test_tickets_resolve_schedules_reflection.py`

**Interfaces:**
- Consumes: `app.learning.reflect.reflect` (Task 3).
- Produces: nothing new for later tasks — this is the final wiring point for the write path.

- [ ] **Step 1: Write the failing test**

```python
"""Confirms POST /tickets/{id}/resolve schedules a reflection background
task with the resolved ticket's id. Does NOT assert on the task actually
running -- BackgroundTasks execution timing under TestClient is an
implementation detail of Starlette, not this endpoint's contract. The
contract is: something got scheduled, for the right ticket, unconditionally
on a successful resolve.
"""
from __future__ import annotations

import uuid

from app.auth.security import hash_password
from app.db.models import EscalationAuthority, Role, User


def _login_helpdesk(client, db_session, *, helpdesk_ref="HD-901"):
    user = User(
        username="resolver", email="resolver@northstar.example", full_name="Resolver",
        password_hash=hash_password("Passw0rd!dev"), role=Role.HELPDESK, helpdesk_ref=helpdesk_ref,
        specialization="Network and VPN Support", escalation_authority=EscalationAuthority.STANDARD,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": "resolver", "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_resolve_schedules_reflection_for_the_resolved_ticket(client, db_session, make_ticket, monkeypatch):
    import app.tickets.router as tickets_router_module

    scheduled: list[uuid.UUID] = []

    def _fake_reflect(ticket_id):
        scheduled.append(ticket_id)

    monkeypatch.setattr(tickets_router_module, "reflect", _fake_reflect)

    headers = _login_helpdesk(client, db_session)
    ticket = make_ticket(assignee_helpdesk_ref="HD-901")

    resp = client.post(f"/api/tickets/{ticket.id}/resolve", json={"resolution": "Fixed it."}, headers=headers)

    assert resp.status_code == 200, resp.text
    assert scheduled == [ticket.id]
```

`BackgroundTasks.add_task(reflect, ticket.id)` schedules `reflect` (the real, imported-into-the-module-namespace function) to run later; `monkeypatch.setattr(tickets_router_module, "reflect", _fake_reflect)` replaces what the module calls **only if `reflect` is imported as a bare name** (`from app.learning.reflect import reflect`) rather than accessed as `learning.reflect.reflect(...)` at call time. Use the bare-name import form in Step 3 specifically so this test's patching works — check how other tests in this codebase patch a module-level import (e.g. `test_admin_dossier.py`'s `monkeypatch.setattr(dossier_module, "_get_sync_client", ...)` pattern patches an attribute of the *dossier* module, which only works because `admin_ticket_dossier` looks up `dossier_module._get_sync_client` fresh each call via its `from app.admin import dossier as dossier_module` local import — mirror that exact style here if a bare top-level import doesn't monkeypatch cleanly.

- [ ] **Step 2: Run and confirm it fails**

Run: `cd backend && uv run pytest tests/test_tickets_resolve_schedules_reflection.py -v`
Expected: FAIL — `assert [] == [ticket.id]` (nothing scheduled yet) or an `AttributeError` if `reflect` isn't imported into the router module at all yet.

- [ ] **Step 3: Wire it up**

In `backend/app/tickets/router.py`, add the import near the top (alongside the existing imports):

```python
from fastapi import BackgroundTasks

from app.learning.reflect import reflect
```

Modify `resolve_ticket_endpoint` (currently around line 196-224):

```python
@router.post("/{ticket_id}/resolve", response_model=TicketDetail)
def resolve_ticket_endpoint(
    ticket_id: uuid.UUID, payload: ResolveTicketRequest, principal: StaffPrincipal, db: DbSession,
    background_tasks: BackgroundTasks,
) -> TicketDetail:
    ticket = load_readable_ticket(db, principal, ticket_id)

    try:
        resolve_ticket(
            db, ticket, resolution=payload.resolution,
            resolved_by_user_id=uuid.UUID(principal.user_id) if principal.user_id else None,
        )
    except InvalidTransition as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    actor_type, actor_id = actor_from_principal(principal)
    record_audit(
        db, actor_type=actor_type, actor_id=actor_id, action="ticket.resolve",
        target_type="ticket", target_id=str(ticket.id),
        payload={"resolution": ticket.resolution},
    )
    db.commit()
    db.refresh(ticket)
    # Scheduled AFTER commit: reflect() opens its own session and reads the
    # ticket by id, so the resolution must already be durably committed
    # before the background task can possibly run (BackgroundTasks fires
    # after the response, but "after commit" is the real invariant that
    # matters here, not "after response").
    background_tasks.add_task(reflect, ticket.id)
    return serialize_detail(ticket)
```

Note the added `background_tasks: BackgroundTasks` parameter — FastAPI injects this the same way for both sync and async endpoints; `resolve_ticket_endpoint` stays a sync `def`, no change to that.

- [ ] **Step 4: Run and confirm it passes**

Run: `cd backend && uv run pytest tests/test_tickets_resolve_schedules_reflection.py -v`
Expected: 1 passed.

- [ ] **Step 5: Run the existing ticket resolve tests to confirm nothing broke**

Run: `cd backend && uv run pytest tests/test_tickets_router_resolve.py -v`
Expected: all still passing (this file's name is inferred from the earlier-seen `test_blank_resolution_is_422` test; if the actual filename differs, find it with `grep -rl "def test_blank_resolution_is_422" backend/tests/` and run that file instead).

- [ ] **Step 6: Commit**

```bash
git add backend/app/tickets/router.py backend/tests/test_tickets_resolve_schedules_reflection.py
git commit -m "Schedule reflection as a background task on ticket resolution"
```

---

## Task 5: Admin lesson edit/archive re-embed; `search_lessons` respects `status`

**Files:**
- Modify: `backend/app/admin/router.py`, `backend/app/agent/tools/knowledge.py`
- Test: `backend/tests/test_admin_lessons_reembed.py`, `backend/tests/test_agent_tools_knowledge.py`

**Interfaces:**
- Consumes: `writer.upsert_embedding` (Task 3).
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_admin_lessons_reembed.py`:

```python
"""admin_patch_lesson and admin_archive_lesson must re-embed on every
change (design spec 13: "edit (re-embedding on save)"), and roll back if
the embed fails (design decision D8) -- otherwise the DB and Chroma can
disagree about a lesson's content or status, which is exactly the
retrieval-poisoning risk archiving exists to prevent.
"""
from __future__ import annotations

import uuid

from app.auth.security import hash_password
from app.db.models import Lesson, LessonConfidence, LessonStatus, Role, User


def _login_admin(client, db_session):
    user = User(
        username="lessonadmin", email="lessonadmin@northstar.example", full_name="Lesson Admin",
        password_hash=hash_password("Passw0rd!dev"), role=Role.ADMIN,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": "lessonadmin", "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_lesson(db_session, **overrides):
    defaults = dict(
        title="t", category="c", content_md="original body", file_path="/x/y.md",
        applies_to=["a"], confidence=LessonConfidence.MEDIUM,
        status=LessonStatus.ACTIVE, created_by_run_id=uuid.uuid4(),
    )
    defaults.update(overrides)
    lesson = Lesson(**defaults)
    db_session.add(lesson)
    db_session.commit()
    return lesson


def test_patch_reembeds_with_the_new_content(client, db_session, monkeypatch):
    import app.admin.router as admin_router_module

    calls = []

    async def _fake_upsert(lesson_row):
        calls.append(lesson_row.content_md)

    monkeypatch.setattr(admin_router_module.writer, "upsert_embedding", _fake_upsert)

    headers = _login_admin(client, db_session)
    lesson = _make_lesson(db_session)

    resp = client.patch(f"/api/admin/lessons/{lesson.id}", json={"content_md": "revised body"}, headers=headers)

    assert resp.status_code == 200, resp.text
    assert calls == ["revised body"]


def test_archive_reembeds_with_archived_status(client, db_session, monkeypatch):
    import app.admin.router as admin_router_module

    calls = []

    async def _fake_upsert(lesson_row):
        calls.append(lesson_row.status.value)

    monkeypatch.setattr(admin_router_module.writer, "upsert_embedding", _fake_upsert)

    headers = _login_admin(client, db_session)
    lesson = _make_lesson(db_session)

    resp = client.delete(f"/api/admin/lessons/{lesson.id}", headers=headers)

    assert resp.status_code == 200, resp.text
    assert calls == ["archived"]


def test_patch_rolls_back_and_returns_503_when_the_embed_fails(client, db_session, monkeypatch):
    import app.admin.router as admin_router_module

    async def _failing_upsert(lesson_row):
        raise RuntimeError("chroma unreachable")

    monkeypatch.setattr(admin_router_module.writer, "upsert_embedding", _failing_upsert)

    headers = _login_admin(client, db_session)
    lesson = _make_lesson(db_session, content_md="original body")
    lesson_id = lesson.id

    resp = client.patch(f"/api/admin/lessons/{lesson_id}", json={"content_md": "this must not stick"}, headers=headers)

    assert resp.status_code == 503

    db_session.expire_all()
    stored = db_session.query(Lesson).filter(Lesson.id == lesson_id).one()
    assert stored.content_md == "original body"
```

Append to `backend/tests/test_agent_tools_knowledge.py` (find its existing `search_lessons` test(s) first with `grep -n "search_lessons" backend/tests/test_agent_tools_knowledge.py` and match its existing fixture/mocking style rather than inventing a new one — the test below assumes a `FakeRagBackend`-style double already exists in that file; adapt the exact construction to match):

```python
async def test_search_lessons_only_queries_active_status(monkeypatch):
    import app.agent.tools.knowledge as knowledge_module

    captured_where = {}

    class _CapturingBackend:
        async def query(self, collection, query_text, where, k):
            captured_where.update(where or {})
            return {"ids": [], "documents": [], "metadatas": [], "distances": []}

    monkeypatch.setattr(knowledge_module, "get_rag_backend", lambda: _CapturingBackend())

    from app.agent.tools.knowledge import SearchLessonsArgs, search_lessons_handler

    await search_lessons_handler(principal=None, db=None, args=SearchLessonsArgs(query="vpn"))

    assert captured_where == {"status": "active"}
```

If the existing file's tests construct `principal`/`db` differently (a real `Principal` instance, a real session) rather than passing `None`, match that existing pattern instead — `search_lessons_handler` doesn't currently use either parameter, so `None` is a reasonable minimal fixture, but consistency with the rest of the file matters more than this test's specific choice.

- [ ] **Step 2: Run and confirm these fail**

Run: `cd backend && uv run pytest tests/test_admin_lessons_reembed.py tests/test_agent_tools_knowledge.py -v -k "reembed or only_queries_active"`
Expected: FAIL — `admin_router_module.writer` doesn't exist yet (no import), and `search_lessons` currently queries `where={}`.

- [ ] **Step 3: Import `writer` into `admin/router.py` and update the two endpoints**

Add near the top of `backend/app/admin/router.py`, alongside existing imports:

```python
from app.learning import writer
```

Modify `admin_patch_lesson` (currently around line 635-660) to become `async def` and re-embed before commit:

```python
@router.patch("/lessons/{lesson_id}", response_model=LessonSummary)
async def admin_patch_lesson(
    lesson_id: uuid.UUID, payload: LessonPatch, principal: AdminPrincipal, db: DbSession,
) -> dict:
    """Same single-transaction contract as admin_patch_user, extended:
    flush the change, re-embed, THEN commit -- not the other way round.
    Spec 13 requires editing to re-embed on save; if the embed fails, the
    edit is rolled back too (design decision D8), because a committed edit
    whose embedding never landed is exactly the DB/Chroma disagreement
    archiving exists to prevent, just via a different failure mode."""
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id).one_or_none()
    if lesson is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such lesson")

    for field in ("content_md", "title", "status"):
        value = getattr(payload, field)
        if value is not None:
            setattr(lesson, field, value)
    db.flush()

    record_audit(
        db, actor_type=ActorType.USER, actor_id=principal.user_id,
        action="lesson.updated", target_type="lesson", target_id=str(lesson.id),
        payload={"status": lesson.status.value},
    )

    try:
        await writer.upsert_embedding(lesson)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "could not update the lesson's embedding; try again") from exc

    db.commit()
    return _lesson_row(lesson)
```

Modify `admin_archive_lesson` (currently around line 663-695) the same way:

```python
@router.delete("/lessons/{lesson_id}", response_model=LessonDeleteResult)
async def admin_archive_lesson(
    lesson_id: uuid.UUID, principal: AdminPrincipal, db: DbSession,
) -> dict:
    """DELETE archives; it does not remove the row. [existing docstring
    content unchanged -- keep it, this only adds the re-embed step.]"""
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id).one_or_none()
    if lesson is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such lesson")

    lesson.status = LessonStatus.ARCHIVED
    db.flush()
    record_audit(
        db, actor_type=ActorType.USER, actor_id=principal.user_id,
        action="lesson.archived", target_type="lesson", target_id=str(lesson.id),
        payload={},
    )

    try:
        await writer.upsert_embedding(lesson)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "could not update the lesson's embedding; try again") from exc

    db.commit()
    return {"id": str(lesson.id), "status": lesson.status.value, "archived": True}
```

Preserve every existing docstring word for word — only the body changes (converting `def` to `async def`, adding the `try/await/except` block before `db.commit()`). Do not touch `admin_patch_user` or any other endpoint in this router; only these two.

- [ ] **Step 4: Update `search_lessons_handler`**

In `backend/app/agent/tools/knowledge.py`, change:

```python
result = await backend.query("lessons", args.query, where={}, k=k)
```

to:

```python
result = await backend.query("lessons", args.query, where={"status": "active"}, k=k)
```

- [ ] **Step 5: Run and confirm the new and existing tests pass**

Run: `cd backend && uv run pytest tests/test_admin_lessons_reembed.py tests/test_agent_tools_knowledge.py tests/test_admin_mutations.py -v`
Expected: all passed. `test_admin_mutations.py` is included because it has pre-existing PATCH-lesson tests from Phase 8a that must survive becoming `async def` — if any fail because they call the endpoint function directly rather than through the `client` fixture (which handles async endpoints transparently via Starlette's `TestClient`), that specific test needs the same `await`/`async def` treatment; check the failure message before changing anything.

- [ ] **Step 6: Commit**

```bash
git add backend/app/admin/router.py backend/app/agent/tools/knowledge.py backend/tests/test_admin_lessons_reembed.py backend/tests/test_agent_tools_knowledge.py
git commit -m "Re-embed on lesson edit and archive; search_lessons excludes archived"
```

---

## Task 6: The live gate test and the closing full-suite run

**Files:**
- Create: `backend/tests/test_learning_live.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: `app.learning.reflect.reflect`, or its constituent pieces, against the real Anthropic API.

- [ ] **Step 1: Register the marker**

In `backend/pyproject.toml`, find the `markers = [` list (currently ending with the `live_dossier` line) and add:

```toml
    "live_reflection: runs one real reflection through the Anthropic API and costs real money -- excluded from the default run",
```

Also add `and not live_reflection` to the `addopts` line's existing `-m "not live_api and not live_smtp and not live_gemini and not live_dossier"` string, making it `"not live_api and not live_smtp and not live_gemini and not live_dossier and not live_reflection"`.

- [ ] **Step 2: Write the live test**

```python
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

import uuid

import pytest

from app.db.session import get_sessionmaker

pytestmark = pytest.mark.live_reflection

_committed: dict[str, list[uuid.UUID]] = {"conversations": [], "runs": [], "tasks": [], "tickets": [], "lessons": []}


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
                run_ids = [r.id for r in s.query(Run).filter(Run.conversation_id.in_(conv_ids)).all()]
                if run_ids:
                    s.query(Span).filter(Span.run_id.in_(run_ids)).delete(synchronize_session=False)
                s.query(Ticket).filter(Ticket.conversation_id.in_(conv_ids)).delete(synchronize_session=False)
                s.query(Task).filter(Task.conversation_id.in_(conv_ids)).delete(synchronize_session=False)
                if run_ids:
                    s.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
                s.query(Message).filter(Message.conversation_id.in_(conv_ids)).delete(synchronize_session=False)
                s.query(Conversation).filter(Conversation.id.in_(conv_ids)).delete(synchronize_session=False)
            s.commit()
    finally:
        import shutil
        from app.learning.writer import KNOWLEDGE_LESSONS_DIR
        # Only remove files this test created, never the whole directory --
        # other lessons may already live there from real use of the app.
        for path in getattr(_sweep_committed_rows_after_module, "_written_files", []):
            try:
                (KNOWLEDGE_LESSONS_DIR / path).unlink(missing_ok=True)
            except Exception:
                pass


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
            resolution_path=ResolutionPath.TICKET,
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
    from app.db.models import Lesson as DbLesson
    from app.learning.reflect import gather_material, build_lesson, _get_client
    from app.learning.writer import create_lesson

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

        assert db_lesson.embedded_at is not None
        import os
        assert os.path.exists(db_lesson.file_path)

    search_result = await search_lessons_handler(
        principal=None, db=None, args=SearchLessonsArgs(query="VPN certificate rejected after renewal"),
    )
    assert any(
        f"lessons/{db_lesson.id}" in wrapped or str(db_lesson.id) in wrapped
        for wrapped in search_result["lessons"]
    ), f"the newly-embedded lesson was not retrieved: {search_result}"
```

- [ ] **Step 3: Run it once, deliberately, against the real API**

Run: `cd backend && uv run pytest tests/test_learning_live.py -v -m live_reflection --no-header`
Expected: 1 passed (or 1 skipped, with the skip reason printed, if the model judged the fixture routine — if skipped, strengthen `_build_committed_ticket`'s scenario to sound less routine and rerun once before accepting a skip as the final answer for this task).

Report the exact output — this is the phase's own gate, the same way `test_admin_dossier_live.py` was Phase 8a's.

- [ ] **Step 4: Run the full default suite to confirm nothing regressed**

Run: `cd backend && uv run python tasks.py test`
Expected: all passing, matching the current baseline plus this phase's new tests, `live_reflection` excluded from the count per `addopts`.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_learning_live.py backend/pyproject.toml
git commit -m "Add the phase 9 live gate: a real reflection retrieved by search_lessons"
```

---

## Self-Review

**Spec coverage:**

| Design spec section | Task |
|---|---|
| §2 D1 BackgroundTasks trigger | 4 |
| §2 D2 async reflect, span() cost tracking | 0 (verify), 2 |
| §2 D3 own DB session, sync-in-async convention | 3 |
| §2 D4 every reflection traced regardless of should_record | 2, 3 |
| §2 D5 failure never surfaces beyond log + run status | 2, 3 |
| §2 D6 file written once, edits don't touch it | 1, 5 (edits confirmed not to touch the file) |
| §2 D7 one upsert path for create/edit/archive/unarchive | 3, 5 |
| §2 D8 embed before commit, roll back on failure | 5 |
| §2 D9 prompt scope: ticket + task + transcript, not the span tree | 2 |
| §3.2 `Lesson` model, exact fields | 2 |
| §3.3 markdown document shape | 1 |
| §3.4 embedding + metadata + `search_lessons` status filter | 3, 5 |
| §3.5 admin endpoints re-embed | 5 |
| §4 error handling (all four bullet points) | 2, 3, 5 |
| §5 testing (unit, integration, live) | 0, 1, 2, 3, 4, 5, 6 |
| Parent spec §18 phase 9 gate | 6 |

No spec section is unclaimed.

**Placeholder scan:** Every step carries real code or a real command. The one spot that looks underspecified — Task 3 Step 7's "fix `build_lesson`'s return shape" — is deliberately a correction-in-place rather than a placeholder: it names the exact bug (a fresh, untracked `uuid.uuid4()` passed as a foreign key that Postgres will reject at commit), why it matters, and what shape the fix takes, leaving only the wrapper's exact name to the implementer's judgment — which Task 6 explicitly accounts for by not hardcoding that name.

**Type consistency:** `Lesson` (pydantic, `app.learning.reflect`) vs `Lesson`/`DbLesson` (SQLAlchemy, `app.db.models`) — every task that imports both aliases the ORM one as `DbLesson` to keep them apart in the same file; Task 3 calls this out explicitly as a naming collision to watch for. `build_lesson`'s return type is `LessonWithRun` (defined in Task 2, a two-field dataclass of `lesson` and `run_id`) from the moment it's first written — Task 2's own tests, Task 3's `reflect()`, and Task 6's live test all access it the same way (`result.lesson`, `result.run_id`), so there is no "built one way, corrected later" step anywhere in the plan. `create_lesson` itself takes the unpacked `lesson`/`run_id` as separate keyword arguments, not a `LessonWithRun`, keeping `writer.py` free of any import from `reflect.py`. `ReflectionMaterial`, `ReflectionFailed`, `gather_material`, `build_lesson`, `reflect`, `upsert_embedding`, `create_lesson`, `slugify`, `render_markdown`, `write_lesson_file`, `KNOWLEDGE_LESSONS_DIR` are each defined exactly once (Tasks 1-3) and referenced with the same name and signature everywhere they're used afterward (Tasks 3, 4, 5, 6).
