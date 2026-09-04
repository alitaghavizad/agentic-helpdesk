# Phase 9 — The Learning Loop — Design Specification

**Date:** 2026-09-04
**Status:** Approved for planning
**Working directory:** `D:\projects\ticketing_full`
**Parent spec:** `docs/superpowers/specs/2026-08-24-agentic-helpdesk-design.md` (§13 learning loop, §5.5 data model, §4.1 module boundaries)

---

## 1. Purpose

Every other phase of this build is done: the agent answers, routes, escalates, gets approved, and gets audited. What it cannot yet do is remember. When a helpdesk specialist resolves a ticket, nothing captures what worked — the next agent facing the same category of problem starts from zero every time.

Phase 9 closes that loop. On ticket resolution, a reflection call asks the model whether this resolution taught something worth keeping; if so, it's written as a lesson, embedded, and becomes retrievable through the `search_lessons` tool the agent already has. The retrieval side (Phase 4) and the admin CRUD side (Phase 8a/8b) already exist — this phase is entirely the write path in between.

### 1.1 Success criteria (parent spec §18, phase 9 gate)

"Resolution writes an `.md`, embeds it, and `search_lessons` retrieves it" — demonstrated end to end against a real ticket and a real model call, not asserted in prose.

### 1.2 Non-goals

- **No lesson quality scoring, ranking, or deduplication beyond what retrieval similarity already gives.** A near-duplicate lesson from a second similar ticket is accepted as-is; curation is the admin's job via the existing edit/archive UI.
- **No automatic lesson expiry.** Lessons don't age out; an admin archives what's stale.
- **No retroactive reflection over already-resolved tickets.** This phase wires the loop going forward only. Backfilling history is a separate, explicit future task if ever wanted.
- **No change to the resolve endpoint's response shape or latency contract.** Reflection must be invisible to the person clicking Resolve.

---

## 2. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Reflection runs via FastAPI `BackgroundTasks`, scheduled from `POST /tickets/{id}/resolve` after commit** | No task queue exists in this project and none is being added for one call per resolution. `BackgroundTasks` is Starlette-native — no new dependency — and runs after the HTTP response is sent, so resolving a ticket never waits on a model call. This is the one deliberate deviation from the dossier's pattern (a user-triggered, waited-for call) — resolution is a routine, frequent staff action, and blocking it on an LLM call the way the dossier blocks on its "generate" button would be a real UX regression the parent spec never asked for. |
| D2 | **`learning.reflect()` is `async def`, not sync** | The dossier's `build_dossier` is sync specifically so its *endpoint* can stay sync and run in Starlette's threadpool without blocking the event loop on a *user-waited* call. Reflection has no such constraint — `BackgroundTasks` runs an async callable directly on the event loop, and `span()`'s context-manager form is **async-only by design** (confirmed in `tracing/spans.py`: "there is no `__enter__`/`__exit__`, so a plain `with span(...):` raises `TypeError`"). Being sync would mean reflection inherits the dossier's actual gap: `build_dossier` never calls `record_usage`, so a dossier's own `Run` carries zero cost/tokens. Being async lets reflection use `span()` properly and get correct cost accounting in the Costs screen's `by_trigger: reflection` row — a deliberate improvement, not parity, and noted here because a future reader diffing this against the dossier should not "fix" it to match. **Verify first, don't assume:** this design requires `AsyncAnthropic().messages.parse(...)` to be an awaitable with the same `output_format=`/`.parsed_output` contract already confirmed for the sync client against the installed `anthropic` package. The implementation plan's first task must confirm this against the real installed version before anything else in `reflect.py` is built on top of it — the same discipline this project has applied to every other SDK-shape claim. |
| D3 | **`reflect(ticket_id)` opens its own DB session** (`get_sessionmaker()()`), a plain sync `Session`, never the caller's | Mirrors `tracing/store.py` and the notification SSE stream: a background task's session must not be the request's, because the request's session is gone by the time a background task runs. `reflect()` takes a plain `uuid.UUID`, not a live ORM object, so it is safe to hand to `BackgroundTasks.add_task` regardless of when it actually executes. Doing sync SQLAlchemy work inside an `async def` is not a new risk introduced here — `agent/tools/*.py`'s handlers already do exactly this on every chat turn, taking `db: Session` and querying it directly from inside `async def` tool handlers — this follows that existing, accepted convention rather than inventing a second one. |
| D4 | **Every reflection is a traced `Run`, whether or not it produces a lesson** | `should_record: false` means "write nothing," not "this didn't happen." The model call was made and billed; it belongs in cost accounting under `RunTrigger.REFLECTION` (already defined in `db/models.py`, unused until now) exactly like any other call. |
| D5 | **A failed reflection never surfaces anywhere but the log and the run's own error status** | Unlike the dossier (a user clicked a button and is waiting for a result), nobody is waiting on a reflection. The resolve response has already gone out. `reflect()` catches everything at its top level, ends its run as `ERROR`, and logs — it does not raise into `BackgroundTasks` where it would produce a scary but harmless traceback, and it must never affect the ticket's resolved state, which is already committed and correct regardless of what reflection does. |
| D6 | **The on-disk `.md` file is written once, at creation; edits touch the database and the embedding, not the file** | The repository layout marks `knowledge/lessons/` as "generated" — a durable, human-readable snapshot of what the system learned, in the spirit of the dossier's audit trail, not a live-synced mirror of the database. Nothing in parent spec §13 requires edits to rewrite the file; it requires "re-embedding on save." Keeping the database row as the single live source of truth (already true today — the admin panel reads and writes `content_md`, never the filesystem) avoids a second write path with its own failure modes for a requirement that doesn't exist. Stated explicitly here so it's a decision, not a gap. |
| D7 | **Archiving and editing both re-run the same embedding upsert, keyed on `status`** | Rather than deleting from Chroma on archive and re-adding on any future unarchive, one `writer.upsert_embedding(lesson)` always pushes the lesson's *current* full state (content + `status` in metadata) to Chroma, and `search_lessons_handler`'s query gains `where={"status": "active"}`. Un-archiving (already possible today via `PATCH .../lessons/{id} {"status": "active"}` — `LessonPatch` already accepts it) then needs no special case: the same upsert makes it retrievable again. One code path for both directions, instead of upsert-for-edit plus delete-for-archive plus a second upsert-for-unarchive. |
| D8 | **The embedding upsert is attempted *before* the database commit in both admin write endpoints, and a failure rolls back both** | `PATCH`/`DELETE` on a lesson are exactly the two places parent spec §13 requires "re-embedding on save" to actually happen. If the embed fails and the DB commit proceeds anyway, `embedded_at`/`status` in Postgres would silently disagree with what the agent actually retrieves — the precise "poisons its own retrieval" risk §13 warns about, now via a stale write instead of a bad model output. Failing the whole request (503, retry-safe since the edit is idempotent) keeps the two stores from drifting, at the cost of one admin action occasionally needing a retry when Chroma is briefly unavailable — an acceptable trade in a single-host dev app where Chroma is already a hard dependency for every other retrieval path. |
| D9 | **The reflection prompt reads the ticket, its `Task`, and the conversation transcript — not the full span tree** | The dossier already exists for deep forensic detail (tool calls, per-span cost, redacted input/output) and is a different consumer for a different reader. A lesson's `situation`/`what_worked`/`what_to_do_differently` fields are about the resolution narrative, which lives in the ticket's own fields and the conversation that produced it. Pulling the full trace would inflate the prompt for no benefit `Lesson`'s five content fields could use. |

---

## 3. Architecture

```
backend/app/learning/
├── reflect.py      # gather_material, build_lesson, reflect(ticket_id) — the public entrypoint
└── writer.py        # slugify, render_markdown, write_lesson_file, upsert_embedding, create_lesson
```

Matches parent spec §16's repository layout exactly (`app/learning/ reflect.py writer.py`) and §4.1's module table (`learning | lessons | reflect(ticket_id)`).

### 3.1 Data flow

```
POST /tickets/{id}/resolve
  └─ resolve_ticket() [existing, unchanged] → commit
  └─ background_tasks.add_task(reflect, ticket.id)
  └─ 200 response sent  ─────────────────────────────────────────► (staff member is done)

  [after the response, on the event loop]
  reflect(ticket_id)
    ├─ open own session
    ├─ load Ticket + Task, gather_material() → prompt content, conversation_id
    ├─ start_run(REFLECTION, conversation_id)
    ├─ async with span(LLM, "messages.parse"):
    │     response = await client.messages.parse(..., output_format=Lesson)
    │     recorder.record_usage(model=..., input_tokens=..., output_tokens=...)
    ├─ end_run(OK | ERROR)
    └─ if parsed.should_record:
          writer.create_lesson(db, ticket=ticket, lesson=parsed, run_id=handle.run_id)
            ├─ render_markdown() → frontmatter + body
            ├─ write_lesson_file() → knowledge/lessons/YYYY-MM-DD-TCK-000123-<slug>.md
            ├─ INSERT lessons row (content_md = full rendered doc, file_path, embedded_at=NULL)
            ├─ await upsert_embedding(lesson)  → Chroma "lessons" collection
            ├─ lesson.embedded_at = now()
            └─ commit
       else:
          log "reflection did not record a lesson for ticket {number}: {reason}"
```

### 3.2 The `Lesson` reflection model

Exactly as specified in parent spec §13 — no changes:

```python
class Lesson(BaseModel):
    should_record: bool
    title: str
    category: str
    situation: str
    what_worked: str
    what_to_do_differently: str
    applies_to: list[str]
    confidence: Literal["low", "medium", "high"]
```

`should_record: false` is the expected common case — most tickets are routine and teach nothing new. The prompt instructs the model accordingly, matching §13's own framing: recording a lesson from every password reset poisons retrieval with noise.

### 3.3 The markdown document

Written once, to `knowledge/lessons/YYYY-MM-DD-TCK-{ticket_number:06d}-{slug(title)}.md`:

```markdown
---
title: <lesson.title>
category: <lesson.category>
confidence: <lesson.confidence>
applies_to: [<lesson.applies_to items>]
ticket: TCK-000123
created_at: 2026-09-04T14:22:01Z
---

## Situation

<lesson.situation>

## What worked

<lesson.what_worked>

## What to do differently

<lesson.what_to_do_differently>
```

`content_md` in the `lessons` table stores this exact document — frontmatter and body together — so the admin panel's edit view and the on-disk file agree at creation time, and so re-embedding always embeds the same text a human would read on disk.

### 3.4 Embedding

`writer.upsert_embedding(lesson)` calls `backend.upsert("lessons", ids=[str(lesson.id)], documents=[lesson.content_md], metadatas=[{...}])` where metadata is:

```python
{
    "lesson_id": str(lesson.id),
    "title": lesson.title,
    "category": lesson.category,
    "confidence": lesson.confidence.value,
    "applies_to": ", ".join(lesson.applies_to),   # Chroma metadata values must be scalar
    "status": lesson.status.value,
}
```

`search_lessons_handler` (existing, `agent/tools/knowledge.py`) changes its query from `where={}` to `where={"status": "active"}` — the one-line change that makes archiving actually remove a lesson from what the agent can retrieve, closing the gap between the DB's archive semantics and Chroma's, which today has no status filter at all.

### 3.5 Admin endpoints gain re-embedding

`admin_patch_lesson` and `admin_archive_lesson` (`app/admin/router.py`, existing, unchanged in shape) become `async def` and, per D8, call `await writer.upsert_embedding(lesson)` before `db.commit()`, with the whole request rolled back and a 503 raised if the embed fails.

---

## 4. Error handling

- **Reflection's own model call fails or returns an unparseable result** (matching `build_dossier`'s two failure branches: `ValidationError`, and a 200 with no `parsed_output`): the run ends `ERROR` with the reason, `reflect()` logs it, and returns. No lesson, no exception escapes.
- **`should_record: false`**: run ends `OK`, no lesson, logged at info level for visibility during development.
- **Writing the lesson fails partway** (file write succeeds, DB insert fails, say): `create_lesson` rolls back its DB transaction; the orphaned file on disk is a cosmetic issue, not a correctness one — the DB row is what makes a lesson real to the rest of the system, and a future re-run of `reflect()` for a *different* ticket would never collide with it (filenames are ticket-scoped).
- **Chroma is unreachable during `create_lesson`**: the DB row exists but `embedded_at` stays `NULL` — an honest, queryable signal ("this lesson exists but isn't retrievable yet"), matching this codebase's existing pattern of representing "unknown/not yet done" as `NULL` rather than fabricating success (e.g. `cost_usd: NULL` for an unpriced model, per parent spec §17).

---

## 5. Testing

- **Unit** — `writer.py`: slugify, markdown rendering (frontmatter fields present and correctly typed, body sections in order), file path format. `reflect.py`: `gather_material` assembles the right prompt content from a built ticket/task/conversation fixture; `build_lesson` against a stubbed client (mirroring `test_admin_dossier.py`'s fake) covering both `should_record: true` and `false`, the `ValidationError` path, and the no-`parsed_output` path; `reflect()` end-to-end against the stub, asserting a `Run` row exists either way and a `Lesson` row exists only when `should_record` is true.
- **Integration** — `POST /tickets/{id}/resolve` schedules the background task (assert `BackgroundTasks.add_task` was called with the right ticket id, via FastAPI's dependency override, rather than asserting on real background execution timing). `admin_patch_lesson`/`admin_archive_lesson` re-embed on success and roll back + 503 on a stubbed embed failure. `search_lessons_handler` only returns `active` lessons — an archived lesson's content must not appear.
- **Live** (`live_reflection` marker, excluded from the default run, following `test_admin_dossier_live.py`'s exact pattern — committing sessions, module-scoped sweep): resolves one real ticket, calls `reflect()` directly against the real Anthropic API, confirms a `Lesson` is recorded (or documents that `should_record` was false and the test cannot proceed further — the model's judgment isn't something this test controls), confirms the file exists on disk, confirms `search_lessons` retrieves it from a real Chroma query. **This is the only test that proves the phase 9 gate**, exactly as the dossier's live test is the only proof a real model can fill `IncidentDossier`.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| The model records a lesson from routine noise, degrading retrieval over time | `should_record` defaults toward *not* recording per the prompt's own framing (inherited from §13); lessons are admin-reviewable and archivable via the existing panel, which is the intended long-term curation mechanism, not automatic filtering. |
| `BackgroundTasks` silently drops the task if the process restarts between commit and execution | Accepted for a single-host dev app with no task queue — the loss is one missed lesson, not data corruption, and nothing downstream assumes reflection always ran. |
| A lesson references sensitive ticket content, and `applies_to`/`category`/`title` end up in Chroma metadata without redaction | Lessons are drafted by the model from the ticket, task, and conversation — the same material a helpdesk specialist already had full legitimate access to; no new disclosure surface. The rendered document is human-reviewable by an admin before anyone else ever sees it in a lesson block. |
| Reflecting on every resolution adds cost per ticket | `effort: "medium"` per parent spec §8.1's own model-configuration table (not `high`, reserved for the chat agent and the dossier); tracked under `RunTrigger.REFLECTION` in the existing Costs screen's `by_trigger` breakdown, so spend is visible, not hidden. |

---

## 7. Open items

None blocking. `ANTHROPIC_API_KEY` is already required project-wide; no new secret or service is introduced.
