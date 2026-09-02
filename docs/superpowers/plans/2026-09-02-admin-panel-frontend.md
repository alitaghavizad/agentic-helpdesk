# Phase 8b — Admin Panel Frontend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the React SPA that consumes the Phase 8a API — login, chat, tickets, and the nine admin screens of parent spec §15 — plus the four backend additions those screens require.

**Architecture:** A Vite + React + TypeScript SPA at `frontend/`, cross-origin to the FastAPI backend on `http://localhost:8000`. Types are generated from the backend's own `/openapi.json`; the HTTP transport, the 401-refresh mutex and the SSE reader are hand-written because no generator produces them well. `src/api/` is the only code that knows about HTTP; `src/pages/` is the only code that knows about layout; typed hooks are the only thing crossing between them.

**Tech Stack:** React 19, React Router 7, TanStack Query 5, Tailwind CSS 4, Vite 8, TypeScript 7, `openapi-typescript` 7, Vitest 4 + Testing Library + MSW 2, Playwright 1.6x. Backend changes use the existing FastAPI/SQLAlchemy/pytest stack.

**Spec:** `docs/superpowers/specs/2026-09-02-admin-panel-frontend-design.md`

## Global Constraints

- **Never use `EventSource`.** Every backend route authenticates from an `Authorization: Bearer` header; `EventSource` cannot send one. All three streams go through `src/api/sse.ts` (spec D1).
- **Never put the access token in `localStorage` or `sessionStorage`.** It lives in memory only; session restore is `POST /api/auth/refresh` with the httpOnly cookie (spec §6.1).
- **`src/api/schema.d.ts` is generated. Never hand-edit it.** Regenerate with `npm run api:generate`.
- **No `fetch(` and no URL string outside `src/api/`.** Pages call hooks; hooks call `src/api/endpoints/*`.
- **No component library, no Radix, no charting library** (spec D5, §5.3).
- **`cost_usd: null` renders the string `unpriced`, never `$0.00`** (parent spec §17).
- **Backend response models are declarations, not transformations.** Routers keep building payloads field by field; adding a `response_model` must never *silently* filter or reshape an existing response body. The one deliberate exception is `PATCH /api/admin/lessons/{id}`, which task 0 widens from `{id, status}` to the full `LessonSummary` so the screen can re-render the edited row — an intentional, stated widening, not a side effect of declaring a model.
- Backend tests: `cd backend && uv run python tasks.py test` (~4-7 min) or `cd backend && uv run pytest tests/<file> -v` for one file.
- Frontend tests: `cd frontend && npm test`. Type check: `cd frontend && npx tsc --noEmit`.
- `make` is not installed on this machine. Use `uv run python tasks.py <task>`.
- Postgres (`docker start postgres18`) and Chroma must be running for backend tests. Docker Desktop lives at `%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe`.
- Commit after every task. Commit messages describe the behaviour change, not the file list.

---

## File Structure

**Backend (task 0 only):**

| File | Responsibility |
|---|---|
| `backend/app/chat/schemas.py` (create) | `MessageView` — the one shared message shape, imported by both the chat and admin routers so they cannot drift |
| `backend/app/chat/router.py` (modify) | `ConversationResponse` gains `messages`; `_serialize` takes the rows |
| `backend/app/admin/schemas.py` (modify) | `ConversationDetail`, `UserPatchResult`, `LessonDeleteResult` |
| `backend/app/admin/queries.py` (modify) | `conversation_detail(db, conversation_id)` — the pure query behind the new endpoint |
| `backend/app/admin/router.py` (modify) | `GET /conversations/{id}`; `response_model` on four existing routes |
| `backend/scripts/dump_openapi.py` (create) | Writes `frontend/openapi.json`. No server, no database |
| `backend/tests/test_chat_transcript.py` (create) | Transcript exposure and its scoping |
| `backend/tests/test_admin_conversation_detail.py` (create) | The new admin endpoint |
| `backend/tests/test_openapi_schemas.py` (create) | The four endpoints publish real schemas, not `object` |

**Frontend:**

| File | Responsibility |
|---|---|
| `src/api/schema.d.ts` | Generated. Never edited |
| `src/api/client.ts` | `apiFetch`, the refresh mutex, `ApiError`. No React |
| `src/api/sse.ts` | `readSse` — frame splitting only. No React, no HTTP policy |
| `src/api/endpoints/{auth,chat,tickets,notifications,admin}.ts` | One typed function per endpoint |
| `src/auth/AuthContext.tsx` | Token in memory, principal, login/guest/logout, boot refresh |
| `src/auth/RequireRole.tsx` | Route guard |
| `src/components/*` | `Table`, `Badge`, `Pager`, `Modal`, `StateBlock`, `JsonBlock`, `NavBar` |
| `src/hooks/*` | `useNotifications`, `useRunStream`, `useChatTurn` |
| `src/lib/format.ts` | `usd`, `tokens`, `duration`, `dateTime` |
| `src/pages/*` | The twelve routes |
| `tests/e2e/*.spec.ts` | Playwright, against the real backend |

---

## Task 0: Backend gaps and the OpenAPI dump

**Files:**
- Create: `backend/app/chat/schemas.py`, `backend/scripts/dump_openapi.py`, `backend/tests/test_chat_transcript.py`, `backend/tests/test_admin_conversation_detail.py`, `backend/tests/test_openapi_schemas.py`
- Modify: `backend/app/chat/router.py`, `backend/app/admin/schemas.py`, `backend/app/admin/queries.py`, `backend/app/admin/router.py`

**Interfaces:**
- Produces: `MessageView {id: str, role: str, content: Any, created_at: str | None, run_id: str | None}`; `ConversationResponse` gains `messages: list[MessageView]`; `ConversationDetail {conversation: ConversationSummary, messages: list[MessageView], runs: list[RunSummary]}`; `queries.conversation_detail(db, conversation_id) -> ConversationDetail | None`; `UserPatchResult {id, role, clearance}`; `LessonDeleteResult {id, status}`; `GET /api/admin/conversations/{conversation_id}`.
- Consumes: existing `chat.service.get_conversation`, `chat.service.load_history`, `admin.dossier.IncidentDossier`, `admin.schemas.{ConversationSummary, RunSummary, LessonSummary}`.

- [ ] **Step 1: Write the failing transcript tests**

Create `backend/tests/test_chat_transcript.py`:

```python
from __future__ import annotations

from app.auth.security import hash_password
from app.db.models import Conversation, MessageRole, Role, User
from app.chat.service import append_message


def _login(client, db_session, *, username: str, role: Role = Role.EMPLOYEE):
    user = User(
        username=username, email=f"{username}@northstar.example",
        full_name=username.title(), password_hash=hash_password("Passw0rd!dev"), role=role,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": username, "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return user, {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_get_conversation_returns_its_transcript(client, db_session):
    user, headers = _login(client, db_session, username="transcript_owner")
    conv = Conversation(user_id=user.id, title="VPN help")
    db_session.add(conv)
    db_session.commit()
    append_message(db_session, conv.id, MessageRole.USER, [{"type": "text", "text": "hello"}])
    append_message(db_session, conv.id, MessageRole.ASSISTANT, [{"type": "text", "text": "hi"}])

    body = client.get(f"/api/conversations/{conv.id}", headers=headers).json()

    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["content"] == [{"type": "text", "text": "hello"}]
    assert body["messages"][0]["created_at"] is not None


def test_transcript_excludes_system_messages(client, db_session):
    user, headers = _login(client, db_session, username="transcript_system")
    conv = Conversation(user_id=user.id, title="t")
    db_session.add(conv)
    db_session.commit()
    append_message(db_session, conv.id, MessageRole.SYSTEM, [{"type": "text", "text": "secret prompt"}])
    append_message(db_session, conv.id, MessageRole.USER, [{"type": "text", "text": "visible"}])

    body = client.get(f"/api/conversations/{conv.id}", headers=headers).json()

    assert [m["role"] for m in body["messages"]] == ["user"]


def test_transcript_is_not_readable_by_another_employee(client, db_session):
    owner, _ = _login(client, db_session, username="transcript_a")
    conv = Conversation(user_id=owner.id, title="private")
    db_session.add(conv)
    db_session.commit()
    append_message(db_session, conv.id, MessageRole.USER, [{"type": "text", "text": "private text"}])
    _, other_headers = _login(client, db_session, username="transcript_b")

    resp = client.get(f"/api/conversations/{conv.id}", headers=other_headers)

    assert resp.status_code == 404
    assert "private text" not in resp.text


def test_list_conversations_does_not_carry_transcripts(client, db_session):
    """The list endpoint shares ConversationResponse. Loading every
    transcript to render a sidebar would read the whole message table."""
    user, headers = _login(client, db_session, username="transcript_list")
    conv = Conversation(user_id=user.id, title="t")
    db_session.add(conv)
    db_session.commit()
    append_message(db_session, conv.id, MessageRole.USER, [{"type": "text", "text": "body text"}])

    body = client.get("/api/conversations", headers=headers).json()

    assert body[0]["messages"] == []
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && uv run pytest tests/test_chat_transcript.py -v`
Expected: FAIL — `KeyError: 'messages'` on the first three, and the last one errors the same way.

- [ ] **Step 3: Add `MessageView` and wire it into the chat router**

Create `backend/app/chat/schemas.py`:

```python
"""The message shape shared by the chat and admin routers.

It lives here rather than in either router because both publish it: the
owner's transcript (GET /api/conversations/{id}) and the admin's
(GET /api/admin/conversations/{id}) are the same rows seen by different
callers, and two independently-maintained copies of that shape would
drift the moment one of them gained a field.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.db.models import Message, MessageRole


class MessageView(BaseModel):
    """`content` is the stored content-block list exactly as the model
    exchanged it -- text blocks, image blocks, tool results. It is `Any`
    because that union is defined by the Anthropic API, not by us, and
    narrowing it here would silently drop block kinds we do not yet know."""
    id: str
    role: str
    content: Any
    created_at: str | None
    run_id: str | None


def to_message_view(message: Message) -> MessageView:
    return MessageView(
        id=str(message.id),
        role=message.role.value,
        content=message.content,
        created_at=message.created_at.isoformat() if message.created_at else None,
        run_id=str(message.run_id) if message.run_id else None,
    )


def transcript_of(db, conversation_id) -> list[MessageView]:
    """System messages are excluded, matching chat.service.load_history:
    they carry the system prompt, which is ours and not the requester's."""
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id, Message.role != MessageRole.SYSTEM)
        .order_by(Message.created_at.asc())
        .all()
    )
    return [to_message_view(row) for row in rows]
```

In `backend/app/chat/router.py`, add the import, extend the model, and give `_serialize` an explicit opt-in:

```python
from app.chat.schemas import MessageView, transcript_of


class ConversationResponse(BaseModel):
    id: str
    title: str | None
    status: str
    messages: list[MessageView] = []


def _serialize(conv, messages: list[MessageView] | None = None) -> ConversationResponse:
    """`messages` defaults to empty rather than being loaded here on purpose:
    the list endpoint shares this serializer, and a sidebar of conversations
    must not read the whole message table to render its titles. Only the
    by-id endpoint passes a transcript."""
    return ConversationResponse(
        id=str(conv.id), title=conv.title, status=conv.status.value,
        messages=messages or [],
    )
```

And in `get_conversation_endpoint` only:

```python
    return _serialize(conv, transcript_of(db, conversation_id))
```

- [ ] **Step 4: Run them and watch them pass**

Run: `cd backend && uv run pytest tests/test_chat_transcript.py -v`
Expected: 4 passed.

- [ ] **Step 5: Write the failing admin conversation-detail tests**

Create `backend/tests/test_admin_conversation_detail.py`:

```python
from __future__ import annotations

import uuid

from app.auth.security import hash_password
from app.chat.service import append_message
from app.db.models import Conversation, MessageRole, Role, Run, RunStatus, RunTrigger, User


def _login(client, db_session, *, username: str, role: Role):
    user = User(
        username=username, email=f"{username}@northstar.example",
        full_name=username.title(), password_hash=hash_password("Passw0rd!dev"), role=role,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": username, "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return user, {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_detail_returns_transcript_and_runs(client, db_session):
    _, headers = _login(client, db_session, username="detail_admin", role=Role.ADMIN)
    conv = Conversation(guest_name="Guest", guest_email="g@example.com", title="Printer down")
    db_session.add(conv)
    db_session.commit()
    append_message(db_session, conv.id, MessageRole.USER, [{"type": "text", "text": "printer"}])
    # Two runs, so the ordering assertion below has something to order.
    db_session.add(Run(conversation_id=conv.id, trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK))
    db_session.add(Run(conversation_id=conv.id, trigger=RunTrigger.CHAT_TURN, status=RunStatus.ERROR))
    db_session.commit()

    body = client.get(f"/api/admin/conversations/{conv.id}", headers=headers).json()

    assert body["conversation"]["title"] == "Printer down"
    assert body["conversation"]["guest_email"] == "g@example.com"
    assert [m["content"] for m in body["messages"]] == [[{"type": "text", "text": "printer"}]]
    assert len(body["runs"]) == 2


def test_detail_excludes_runs_from_other_conversations(client, db_session):
    """The screen links each run into the trace view. A run from another
    conversation appearing here would send an admin to the wrong tree."""
    _, headers = _login(client, db_session, username="detail_scope", role=Role.ADMIN)
    mine = Conversation(guest_name="G", guest_email="g@example.com", title="mine")
    other = Conversation(guest_name="G", guest_email="g@example.com", title="other")
    db_session.add_all([mine, other])
    db_session.commit()
    db_session.add(Run(conversation_id=other.id, trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK))
    db_session.commit()

    body = client.get(f"/api/admin/conversations/{mine.id}", headers=headers).json()

    assert body["runs"] == []


def test_detail_404s_for_an_unknown_conversation(client, db_session):
    _, headers = _login(client, db_session, username="detail_404", role=Role.ADMIN)

    resp = client.get(f"/api/admin/conversations/{uuid.uuid4()}", headers=headers)

    assert resp.status_code == 404


def test_detail_is_admin_only(client, db_session):
    _, headers = _login(client, db_session, username="detail_employee", role=Role.EMPLOYEE)
    conv = Conversation(guest_name="G", guest_email="g@example.com", title="t")
    db_session.add(conv)
    db_session.commit()

    resp = client.get(f"/api/admin/conversations/{conv.id}", headers=headers)

    assert resp.status_code == 403
```

- [ ] **Step 6: Run them and watch them fail**

Run: `cd backend && uv run pytest tests/test_admin_conversation_detail.py -v`
Expected: FAIL — 404 from the router on all four, because the route does not exist. (The admin-only test would pass for the wrong reason; the others prove the route is genuinely missing.)

- [ ] **Step 7: Implement the query, the schema and the route**

Append to `backend/app/admin/queries.py`:

```python
def conversation_runs(db: Session, conversation_id) -> list[Run]:
    """Every run belonging to one conversation, newest first, so the detail
    screen can link each into GET /api/admin/runs/{id}/trace.

    Unpaginated, unlike every other list in this module: runs are bounded by
    the turns in a single conversation, not by the size of the table.
    """
    return (
        db.query(Run)
        .filter(Run.conversation_id == conversation_id)
        .order_by(Run.started_at.desc(), Run.id.desc())
        .all()
    )
```

Append to `backend/app/admin/schemas.py`:

```python
class ConversationDetail(BaseModel):
    """GET /conversations/{id}. Parent spec 15 wants the transcript beside
    its span tree, so this returns both halves in one call: the messages,
    and enough of each run to render a row that links into the trace view."""
    conversation: ConversationSummary
    messages: list[MessageView]
    runs: list[RunSummary]


class UserPatchResult(BaseModel):
    id: str
    role: str
    clearance: str | None


class LessonDeleteResult(BaseModel):
    id: str
    status: str
```

with `from app.chat.schemas import MessageView` at the top of that module.

In `backend/app/admin/router.py`, add the route **above** the existing `/conversations` list route is not required — FastAPI matches on the full path, and `/conversations/{conversation_id}` cannot shadow `/conversations`. Place it directly after `admin_conversations`:

```python
@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def admin_conversation_detail(
    conversation_id: uuid.UUID, principal: AdminPrincipal, db: DbSession,
) -> ConversationDetail:
    """Not audited, like every other admin read. The audit log records
    mutating calls (spec 14); a row per detail view would bury real events
    under navigation noise."""
    from app.chat.schemas import transcript_of
    from app.db.models import Conversation

    conv = db.query(Conversation).filter(Conversation.id == conversation_id).one_or_none()
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such conversation")

    return ConversationDetail(
        conversation=ConversationSummary(
            id=str(conv.id), title=conv.title, status=conv.status.value,
            user_id=str(conv.user_id) if conv.user_id else None,
            guest_name=conv.guest_name, guest_email=conv.guest_email,
            created_at=conv.created_at.isoformat() if conv.created_at else None,
        ),
        messages=transcript_of(db, conversation_id),
        runs=[RunSummary(**_run_list_row(run)) for run in queries.conversation_runs(db, conversation_id)],
    )
```

`_run_list_row` is the existing per-row builder used by `admin_runs`. If that mapping is currently inline in `admin_runs`, extract it to a module-level `_run_list_row(run) -> dict` and have `admin_runs` call it, so the two lists cannot disagree about what a `RunSummary` is. Add `ConversationDetail` to the `app.admin.schemas` import list.

- [ ] **Step 8: Run them and watch them pass**

Run: `cd backend && uv run pytest tests/test_admin_conversation_detail.py -v`
Expected: 4 passed.

- [ ] **Step 9: Write the failing schema-publication test**

Create `backend/tests/test_openapi_schemas.py`:

```python
from __future__ import annotations

import pytest

from app.main import app

# Endpoints whose published 200 body must be a real named schema. Each of
# these already returns a fixed, known shape; publishing `object` meant a
# generated client could not tell a dossier from an empty dict.
TYPED = [
    ("post", "/api/admin/tickets/{ticket_id}/dossier", "IncidentDossier"),
    ("patch", "/api/admin/users/{user_id}", "UserPatchResult"),
    ("patch", "/api/admin/lessons/{lesson_id}", "LessonSummary"),
    ("delete", "/api/admin/lessons/{lesson_id}", "LessonDeleteResult"),
    ("get", "/api/admin/conversations/{conversation_id}", "ConversationDetail"),
    ("get", "/api/conversations/{conversation_id}", "ConversationResponse"),
]


@pytest.fixture(scope="module")
def schema():
    return app.openapi()


@pytest.mark.parametrize("method,path,expected", TYPED)
def test_endpoint_publishes_a_named_schema(schema, method, path, expected):
    body = schema["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
    assert body.get("$ref") == f"#/components/schemas/{expected}", body


def test_dossier_schema_carries_its_fields(schema):
    """A $ref to an empty model would satisfy the test above while telling a
    client nothing. The dossier is the one whose 15 fields the UI renders."""
    props = schema["components"]["schemas"]["IncidentDossier"]["properties"]
    for field in ("ticket_number", "problem_statement", "requester", "timeline",
                  "knowledge_sources", "risk_flags", "cost_summary"):
        assert field in props


def test_message_view_is_shared_not_duplicated(schema):
    """Both transcript endpoints must reference the SAME MessageView schema.
    Two structurally-identical copies would generate two TypeScript types
    and let the two endpoints drift apart later."""
    conv = schema["components"]["schemas"]["ConversationResponse"]["properties"]["messages"]
    detail = schema["components"]["schemas"]["ConversationDetail"]["properties"]["messages"]
    assert conv["items"]["$ref"] == detail["items"]["$ref"] == "#/components/schemas/MessageView"
```

- [ ] **Step 10: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/test_openapi_schemas.py -v`
Expected: FAIL — the first four parametrized cases fail with `assert None == '#/components/schemas/...'`, because those routes publish `object`.

- [ ] **Step 11: Declare the four response models**

In `backend/app/admin/router.py`, change only the decorators and return annotations. **Do not touch the bodies** — every payload stays built field by field:

```python
@router.patch("/users/{user_id}", response_model=UserPatchResult)
def admin_patch_user(...) -> dict:

@router.post("/tickets/{ticket_id}/dossier", response_model=IncidentDossier)
def admin_ticket_dossier(...) -> dict:

@router.patch("/lessons/{lesson_id}", response_model=LessonSummary)
def admin_patch_lesson(...) -> dict:

@router.delete("/lessons/{lesson_id}", response_model=LessonDeleteResult)
def admin_archive_lesson(...) -> dict:
```

`IncidentDossier` imports from `app.admin.dossier` at module top level — it is a plain pydantic model with no Anthropic client construction at import time, so this does not make the module require an API key.

`admin_patch_lesson` currently returns `{"id", "status"}` but is now declared as `LessonSummary`, which has more fields. Either widen the return to the full `LessonSummary` (preferred — the screen re-renders the edited row from it) or declare `LessonDeleteResult` for it too. **Widen it**: build the same dict the list endpoint builds for one lesson, extracted into a module-level `_lesson_row(lesson) -> dict` that `admin_lessons` also calls.

- [ ] **Step 12: Run the schema test and the existing admin suites**

Run: `cd backend && uv run pytest tests/test_openapi_schemas.py tests/test_admin_mutations.py tests/test_admin_read_endpoints.py tests/test_admin_dossier.py tests/test_chat_router.py -v`
Expected: all pass. If any existing test fails on a changed response body, the `response_model` is filtering a field the body used to carry — fix by widening the model, never by changing the body.

- [ ] **Step 13: Add the OpenAPI dump script**

Create `backend/scripts/dump_openapi.py`:

```python
"""Writes the live OpenAPI schema to frontend/openapi.json.

The frontend generates its TypeScript types from this file, and both it and
the generated types are committed, so `npm run api:check` can regenerate and
diff them. A generated file nobody regenerates is a file nobody notices
going stale.

Imports the app but starts no server and opens no database connection, so it
runs with Docker stopped and in CI.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.main import app

OUTPUT = Path(__file__).resolve().parent.parent.parent / "frontend" / "openapi.json"


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys so the committed file is stable: without it, a schema whose
    # key order shifts between runs shows as a diff with no change in it.
    OUTPUT.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 14: Run it and confirm the file lands**

Run: `cd backend && uv run python scripts/dump_openapi.py`
Expected: `wrote D:\projects\ticketing_full\frontend\openapi.json`. Confirm with `python -c "import json;d=json.load(open('frontend/openapi.json'));print(len(d['paths']),'paths')"` from the repo root — expect 32 paths (31 before, plus the new admin detail route).

- [ ] **Step 15: Commit**

```bash
git add backend/app/chat/schemas.py backend/app/chat/router.py backend/app/admin/ backend/scripts/dump_openapi.py backend/tests/test_chat_transcript.py backend/tests/test_admin_conversation_detail.py backend/tests/test_openapi_schemas.py frontend/openapi.json
git commit -m "Expose conversation transcripts and type the last four admin responses"
```

---

## Task 1: Frontend scaffold, transport, and the SSE reader

**Files:**
- Create: `frontend/` (scaffold), `frontend/src/api/client.ts`, `frontend/src/api/sse.ts`, `frontend/src/lib/format.ts`, `frontend/src/api/client.test.ts`, `frontend/src/api/sse.test.ts`, `frontend/src/lib/format.test.ts`, `frontend/.env.example`
- Generate: `frontend/src/api/schema.d.ts`

**Interfaces:**
- Produces: `apiFetch<T>(path: string, init?: RequestInit & {raw?: boolean}): Promise<T>`; `ApiError {status: number, detail: string}`; `setAccessToken(token: string | null)`; `getAccessToken(): string | null`; `setAuthFailureHandler(fn: () => void)`; `apiStream(path, init): Promise<Response>`; `readSse(response: Response): AsyncGenerator<Record<string, unknown>>`; `usd(v: number | null): string`; `tokens(v: number | null): string`; `duration(ms: number | null): string`; `dateTime(iso: string | null): string`.
- Consumes: `frontend/openapi.json` from task 0.

- [ ] **Step 1: Scaffold and install**

From the repo root:

```bash
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install react-router-dom @tanstack/react-query
npm install -D tailwindcss @tailwindcss/vite openapi-typescript vitest @vitest/coverage-v8 jsdom @testing-library/react @testing-library/user-event @testing-library/jest-dom msw
```

If `npm create vite` refuses because `frontend/` already contains `openapi.json`, scaffold to `frontend-tmp/`, move its contents into `frontend/`, and delete `frontend-tmp/`.

- [ ] **Step 2: Configure Vite, Tailwind and Vitest**

`frontend/vite.config.ts`:

```ts
// defineConfig comes from "vitest/config", NOT "vite": the plain Vite
// export does not type the `test` key, so a config carrying it fails
// `tsc --noEmit` in step 3 with "Object literal may only specify known
// properties".
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173 },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    globals: true,
  },
});
```

`frontend/src/index.css` starts with `@import "tailwindcss";` and nothing else.

`frontend/src/test-setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
```

`frontend/.env.example`:

```
VITE_API_BASE=http://localhost:8000
```

Copy it to `frontend/.env` locally. `.env` is already git-ignored at the repo root; confirm the ignore covers `frontend/.env` and add it if not.

Add to `frontend/package.json` scripts:

```json
{
  "test": "vitest run",
  "test:watch": "vitest",
  "typecheck": "tsc --noEmit",
  "api:generate": "openapi-typescript openapi.json -o src/api/schema.d.ts",
  "api:check": "cd ../backend && uv run python scripts/dump_openapi.py && cd ../frontend && npm run api:generate && git diff --exit-code openapi.json src/api/schema.d.ts"
}
```

- [ ] **Step 3: Generate the types and confirm the toolchain is clean**

Run: `cd frontend && npm run api:generate && npx tsc --noEmit && npm run build`
Expected: `schema.d.ts` is written, the type check is clean, and the build succeeds. **This is the toolchain gate — three recent majors (Tailwind 4, Vite 8, TypeScript 7) must be proven working here, before any screen depends on them.**

- [ ] **Step 4: Write the failing SSE reader tests**

Create `frontend/src/api/sse.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { readSse } from "./sse";

function responseOf(...chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body);
}

async function collect(response: Response) {
  const out: unknown[] = [];
  for await (const frame of readSse(response)) out.push(frame);
  return out;
}

describe("readSse", () => {
  it("yields one object per frame", async () => {
    const frames = await collect(
      responseOf('data: {"type":"token","text":"a"}\n\ndata: {"type":"done"}\n\n'),
    );
    expect(frames).toEqual([{ type: "token", text: "a" }, { type: "done" }]);
  });

  it("ignores the backend's keepalive comment frames", async () => {
    // The backend sends ": keepalive\n\n" every 15s on both streams.
    const frames = await collect(responseOf(': keepalive\n\ndata: {"type":"done"}\n\n'));
    expect(frames).toEqual([{ type: "done" }]);
  });

  it("reassembles a frame split across two network chunks", async () => {
    const frames = await collect(responseOf('data: {"type":"tok', 'en","text":"x"}\n\n'));
    expect(frames).toEqual([{ type: "token", text: "x" }]);
  });

  it("reassembles a frame whose delimiter is split across chunks", async () => {
    const frames = await collect(responseOf('data: {"a":1}\n', '\ndata: {"a":2}\n\n'));
    expect(frames).toEqual([{ a: 1 }, { a: 2 }]);
  });

  it("drops a truncated final frame rather than throwing", async () => {
    // A stream cut mid-frame must not take the page down with a JSON error.
    const frames = await collect(responseOf('data: {"a":1}\n\ndata: {"a":'));
    expect(frames).toEqual([{ a: 1 }]);
  });

  it("handles a multi-byte character split across chunks", async () => {
    const encoder = new TextEncoder();
    const bytes = encoder.encode('data: {"t":"café"}\n\n');
    const split = 14; // lands inside the é
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, split));
        controller.enqueue(bytes.slice(split));
        controller.close();
      },
    });
    expect(await collect(new Response(body))).toEqual([{ t: "café" }]);
  });
});
```

- [ ] **Step 5: Run them and watch them fail**

Run: `cd frontend && npx vitest run src/api/sse.test.ts`
Expected: FAIL — cannot resolve `./sse`.

- [ ] **Step 6: Implement the reader**

Create `frontend/src/api/sse.ts`:

```ts
/**
 * Reads a text/event-stream response body.
 *
 * Not EventSource: every backend route authenticates from an Authorization
 * header and EventSource cannot send one. Passing the token as a query
 * parameter instead would write JWTs into access logs and Referer headers,
 * so all three streams -- notifications, admin runs, and a chat turn (which
 * is SSE over POST and could never have been EventSource anyway) -- read
 * their bodies here.
 *
 * Frame splitting only. Reconnection, backoff and abort belong to the
 * caller: the three streams want three different policies, and a chat turn
 * must never be retried at all.
 */
export async function* readSse(
  response: Response,
): AsyncGenerator<Record<string, unknown>> {
  if (!response.body) return;
  const reader = response.body.getReader();
  // stream: true so a multi-byte character split across two chunks is held
  // until its remaining bytes arrive rather than decoded as replacement
  // characters.
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      for (;;) {
        const boundary = buffer.indexOf("\n\n");
        if (boundary === -1) break;
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trim())
          .join("\n");
        // Empty means a comment-only frame -- the backend's ": keepalive".
        if (data) yield JSON.parse(data) as Record<string, unknown>;
      }
    }
    // Whatever is left in `buffer` is a frame the stream was cut in the
    // middle of. Dropping it is correct: half a JSON object is not an
    // event, and throwing here would turn a dropped connection into an
    // unhandled rejection in whichever component was listening.
  } finally {
    reader.releaseLock();
  }
}
```

- [ ] **Step 7: Run them and watch them pass**

Run: `cd frontend && npx vitest run src/api/sse.test.ts`
Expected: 6 passed.

- [ ] **Step 8: Write the failing transport tests**

Create `frontend/src/api/client.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, setAccessToken, setAuthFailureHandler } from "./client";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  setAccessToken("token-1");
  setAuthFailureHandler(() => {});
});

afterEach(() => vi.unstubAllGlobals());

describe("apiFetch", () => {
  it("sends the bearer token and credentials", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await apiFetch("/api/health");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/api/health");
    expect(new Headers(init.headers).get("authorization")).toBe("Bearer token-1");
    expect(init.credentials).toBe("include");
  });

  it("refreshes once on 401 and retries with the new token", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401))
      .mockResolvedValueOnce(jsonResponse({ access_token: "token-2" }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    await expect(apiFetch<{ ok: boolean }>("/api/tickets")).resolves.toEqual({ ok: true });

    const retry = fetchMock.mock.calls[2][1];
    expect(new Headers(retry.headers).get("authorization")).toBe("Bearer token-2");
  });

  it("refreshes ONCE for concurrent 401s, not once per request", async () => {
    // The load-bearing assertion of this module. Four screens mount at the
    // same moment on an expired token; without a shared in-flight promise
    // each fires its own refresh, and every refresh after the first
    // presents a token the server has already rotated and revoked -- so
    // three of the four log the user out.
    let refreshes = 0;
    fetchMock.mockImplementation(async (url: string, init: RequestInit) => {
      if (url.endsWith("/api/auth/refresh")) {
        refreshes += 1;
        return jsonResponse({ access_token: "token-2" });
      }
      return new Headers(init.headers).get("authorization") === "Bearer token-2"
        ? jsonResponse({ ok: true })
        : jsonResponse({ detail: "expired" }, 401);
    });

    const results = await Promise.all([
      apiFetch("/api/a"), apiFetch("/api/b"), apiFetch("/api/c"), apiFetch("/api/d"),
    ]);

    expect(refreshes).toBe(1);
    expect(results).toEqual([{ ok: true }, { ok: true }, { ok: true }, { ok: true }]);
  });

  it("calls the auth-failure handler when the refresh itself fails", async () => {
    const onFailure = vi.fn();
    setAuthFailureHandler(onFailure);
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401))
      .mockResolvedValueOnce(jsonResponse({ detail: "no cookie" }, 401));

    await expect(apiFetch("/api/tickets")).rejects.toBeInstanceOf(ApiError);
    expect(onFailure).toHaveBeenCalledOnce();
  });

  it("does not try to refresh a failed refresh call itself", async () => {
    // Otherwise a 401 from /auth/refresh recurses until the stack blows.
    fetchMock.mockResolvedValue(jsonResponse({ detail: "no cookie" }, 401));
    await expect(apiFetch("/api/auth/refresh", { method: "POST" })).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("throws ApiError carrying FastAPI's detail", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "no such ticket" }, 404));
    await expect(apiFetch("/api/tickets/x")).rejects.toMatchObject({
      status: 404,
      detail: "no such ticket",
    });
  });

  it("returns undefined for a 204 rather than choking on an empty body", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(apiFetch("/api/auth/logout", { method: "POST" })).resolves.toBeUndefined();
  });
});
```

- [ ] **Step 9: Run them and watch them fail**

Run: `cd frontend && npx vitest run src/api/client.test.ts`
Expected: FAIL — cannot resolve `./client`.

- [ ] **Step 10: Implement the transport**

Create `frontend/src/api/client.ts`:

```ts
/**
 * The only module that performs HTTP. Deliberately React-free: the auth
 * context pushes the token in through setAccessToken, so this can be tested
 * without rendering anything.
 */
const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
const REFRESH_PATH = "/api/auth/refresh";

export class ApiError extends Error {
  constructor(readonly status: number, readonly detail: string) {
    super(`${status}: ${detail}`);
    this.name = "ApiError";
  }
}

let accessToken: string | null = null;
let onAuthFailure: () => void = () => {};

export function setAccessToken(token: string | null): void {
  accessToken = token;
}
export function getAccessToken(): string | null {
  return accessToken;
}
export function setAuthFailureHandler(handler: () => void): void {
  onAuthFailure = handler;
}

/**
 * One shared in-flight refresh, not one per caller.
 *
 * Several screens mount at once and all 401 together on an expired token.
 * Refresh tokens are single-use and rotated server-side (app/auth/router.py
 * revokes the presented one), so a second concurrent refresh presents a
 * token that was just revoked and fails -- logging the user out in the
 * middle of a successful recovery. Every 401 therefore awaits the same
 * promise.
 */
let refreshInFlight: Promise<string | null> | null = null;

function refreshOnce(): Promise<string | null> {
  if (!refreshInFlight) {
    refreshInFlight = fetch(`${BASE}${REFRESH_PATH}`, {
      method: "POST",
      credentials: "include",
    })
      .then(async (response) => {
        if (!response.ok) return null;
        const body = (await response.json()) as { access_token: string };
        accessToken = body.access_token;
        return body.access_token;
      })
      .catch(() => null)
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

function buildInit(init: RequestInit, token: string | null): RequestInit {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body !== undefined && !(init.body instanceof FormData) && !headers.has("content-type")) {
    headers.set("Content-Type", "application/json");
  }
  return { ...init, headers, credentials: "include" };
}

async function raise(response: Response): Promise<never> {
  let detail = response.statusText;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
    else if (body.detail !== undefined) detail = JSON.stringify(body.detail);
  } catch {
    // A non-JSON error body (a proxy's HTML, an empty 502). statusText stands.
  }
  throw new ApiError(response.status, detail);
}

/** Performs the request, transparently refreshing once on a 401. */
async function send(path: string, init: RequestInit): Promise<Response> {
  const first = await fetch(`${BASE}${path}`, buildInit(init, accessToken));
  // Refreshing a failed refresh would recurse forever.
  if (first.status !== 401 || path === REFRESH_PATH) return first;

  const token = await refreshOnce();
  if (!token) {
    onAuthFailure();
    return first;
  }
  return fetch(`${BASE}${path}`, buildInit(init, token));
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await send(path, init);
  if (!response.ok) await raise(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * Same auth and refresh behaviour, but hands back the Response so the caller
 * can read a stream body. Used only by the three SSE consumers.
 */
export async function apiStream(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await send(path, { ...init, headers: { Accept: "text/event-stream", ...init.headers } });
  if (!response.ok) await raise(response);
  return response;
}
```

- [ ] **Step 11: Run them and watch them pass**

Run: `cd frontend && npx vitest run src/api/client.test.ts`
Expected: 7 passed.

- [ ] **Step 12: Write the failing formatting tests**

Create `frontend/src/lib/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { duration, tokens, usd } from "./format";

describe("usd", () => {
  it("renders 'unpriced' for null, never a zero", () => {
    // Parent spec 17: a model absent from the rate table stores NULL. A
    // confidently wrong $0.00 is worse than saying nothing.
    expect(usd(null)).toBe("unpriced");
  });

  it("keeps sub-cent costs visible", () => {
    // Most single spans cost well under a cent; rounding to 2dp would
    // render a whole trace as a column of $0.00.
    expect(usd(0.000123)).toBe("$0.000123");
    expect(usd(0)).toBe("$0.000000");
  });

  it("switches to two decimals above a dollar", () => {
    expect(usd(12.3456)).toBe("$12.35");
  });
});

describe("tokens", () => {
  it("groups thousands and renders null as a dash", () => {
    expect(tokens(1234567)).toBe("1,234,567");
    expect(tokens(null)).toBe("—");
  });
});

describe("duration", () => {
  it("renders ms, seconds and minutes at readable precision", () => {
    expect(duration(42)).toBe("42ms");
    expect(duration(1500)).toBe("1.5s");
    expect(duration(95000)).toBe("1m 35s");
    expect(duration(null)).toBe("—");
  });
});
```

- [ ] **Step 13: Run them and watch them fail, then implement**

Run: `cd frontend && npx vitest run src/lib/format.test.ts` — FAIL, module missing.

Create `frontend/src/lib/format.ts`:

```ts
export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "unpriced";
  if (Math.abs(value) >= 1) return `$${value.toFixed(2)}`;
  return `$${value.toFixed(6)}`;
}

export function tokens(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("en-US");
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  return `${minutes}m ${Math.round((ms % 60_000) / 1000)}s`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleString();
}
```

Run again: 3 passed.

- [ ] **Step 14: Commit**

```bash
git add frontend
git commit -m "Scaffold the frontend with its transport, SSE reader and generated types"
```

---

## Task 2: Auth context, login, app shell and role routing

**Files:**
- Create: `frontend/src/api/endpoints/auth.ts`, `frontend/src/auth/AuthContext.tsx`, `frontend/src/auth/RequireRole.tsx`, `frontend/src/pages/Login.tsx`, `frontend/src/components/{NavBar,StateBlock,Badge}.tsx`, `frontend/src/auth/AuthContext.test.tsx`, `frontend/src/auth/RequireRole.test.tsx`, `frontend/src/pages/Login.test.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/main.tsx`

**Interfaces:**
- Consumes: `apiFetch`, `setAccessToken`, `setAuthFailureHandler`, `ApiError` (task 1).
- Produces: `useAuth(): {principal: Principal | null, status: "loading" | "signed-in" | "signed-out", login(u, p), loginAsGuest(name, email), logout()}`; `Principal` = `components["schemas"]["PrincipalResponse"]`; `<RequireRole role="admin">`; `landingFor(principal): string`.

- [ ] **Step 1: Write the failing auth tests**

Create `frontend/src/auth/AuthContext.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, landingFor, useAuth } from "./AuthContext";

const fetchMock = vi.fn();
function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}
const ADMIN = { kind: "user", user_id: "u1", role: "admin", clearance: "privileged",
  department: "IT", employee_ref: null, helpdesk_ref: null };

function Probe() {
  const { status, principal } = useAuth();
  return <div data-testid="probe">{status}:{principal?.role ?? "none"}</div>;
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
});

describe("AuthProvider", () => {
  it("restores a session from the refresh cookie on boot", async () => {
    // The access token is memory-only, so a reload starts with nothing and
    // the httpOnly cookie is the ONLY way back into a session.
    fetchMock.mockImplementation(async (url: string) =>
      url.endsWith("/api/auth/refresh") ? jsonResponse({ access_token: "t" }) : jsonResponse(ADMIN),
    );
    render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("signed-in:admin"));
  });

  it("lands signed-out when there is no usable cookie", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "No refresh token" }, 401));
    render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("signed-out:none"));
  });

  it("never writes the token to storage", async () => {
    // An XSS that reads localStorage would get a bearer token outliving the
    // tab. The cookie it cannot read is the point of the whole design.
    fetchMock.mockImplementation(async (url: string) =>
      url.endsWith("/api/auth/refresh") ? jsonResponse({ access_token: "t" }) : jsonResponse(ADMIN),
    );
    render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("signed-in"));
    expect(JSON.stringify(localStorage)).not.toContain("t");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});

describe("landingFor", () => {
  it("routes by role", () => {
    expect(landingFor({ ...ADMIN, role: "admin" })).toBe("/admin");
    expect(landingFor({ ...ADMIN, role: "helpdesk" })).toBe("/tickets");
    expect(landingFor({ ...ADMIN, role: "employee" })).toBe("/chat");
    expect(landingFor({ ...ADMIN, role: "guest" })).toBe("/chat");
  });
});
```

- [ ] **Step 2: Run and watch it fail**

Run: `cd frontend && npx vitest run src/auth/AuthContext.test.tsx`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the endpoints module and the context**

Create `frontend/src/api/endpoints/auth.ts`:

```ts
import { apiFetch } from "../client";
import type { components } from "../schema";

export type Principal = components["schemas"]["PrincipalResponse"];
type TokenResponse = components["schemas"]["TokenResponse"];

export const login = (username: string, password: string) =>
  apiFetch<TokenResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });

export const loginAsGuest = (name: string, email: string) =>
  apiFetch<TokenResponse>("/api/auth/guest", {
    method: "POST",
    body: JSON.stringify({ name, email }),
  });

export const refresh = () => apiFetch<TokenResponse>("/api/auth/refresh", { method: "POST" });
export const logout = () => apiFetch<void>("/api/auth/logout", { method: "POST" });
export const me = () => apiFetch<Principal>("/api/auth/me");
```

Create `frontend/src/auth/AuthContext.tsx`:

```tsx
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { setAccessToken, setAuthFailureHandler } from "../api/client";
import * as auth from "../api/endpoints/auth";
import type { Principal } from "../api/endpoints/auth";

type Status = "loading" | "signed-in" | "signed-out";

interface AuthValue {
  status: Status;
  principal: Principal | null;
  login: (username: string, password: string) => Promise<Principal>;
  loginAsGuest: (name: string, email: string) => Promise<Principal>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function landingFor(principal: Principal): string {
  if (principal.role === "admin") return "/admin";
  if (principal.role === "helpdesk") return "/tickets";
  return "/chat";
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [principal, setPrincipal] = useState<Principal | null>(null);

  const clear = useCallback(() => {
    setAccessToken(null);
    setPrincipal(null);
    setStatus("signed-out");
  }, []);

  const adopt = useCallback(async (token: string) => {
    setAccessToken(token);
    const who = await auth.me();
    setPrincipal(who);
    setStatus("signed-in");
    return who;
  }, []);

  // Boot: the access token is memory-only, so a reload has none. The
  // httpOnly refresh cookie is the only path back into a session -- and
  // guests never receive one (app/auth/router.py issues it for kind ==
  // "user" only), so a guest reload correctly lands signed-out.
  useEffect(() => {
    setAuthFailureHandler(clear);
    let cancelled = false;
    (async () => {
      try {
        const { access_token } = await auth.refresh();
        if (!cancelled) await adopt(access_token);
      } catch {
        if (!cancelled) clear();
      }
    })();
    return () => { cancelled = true; };
  }, [adopt, clear]);

  const value = useMemo<AuthValue>(() => ({
    status,
    principal,
    login: async (username, password) => adopt((await auth.login(username, password)).access_token),
    loginAsGuest: async (name, email) => adopt((await auth.loginAsGuest(name, email)).access_token),
    logout: async () => {
      try { await auth.logout(); } finally { clear(); }
    },
  }), [status, principal, adopt, clear]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
```

- [ ] **Step 4: Run and watch it pass**

Run: `cd frontend && npx vitest run src/auth/AuthContext.test.tsx`
Expected: 4 passed.

- [ ] **Step 5: Write the failing route-guard test**

Create `frontend/src/auth/RequireRole.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { RequireRole } from "./RequireRole";
import * as ctx from "./AuthContext";

function renderAt(path: string, principal: Partial<ctx.Principal> | null, status = "signed-in") {
  vi.spyOn(ctx, "useAuth").mockReturnValue({
    status, principal, login: vi.fn(), loginAsGuest: vi.fn(), logout: vi.fn(),
  } as never);
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/chat" element={<div>chat page</div>} />
        <Route path="/login" element={<div>login page</div>} />
        <Route path="/admin" element={<RequireRole role="admin"><div>admin page</div></RequireRole>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RequireRole", () => {
  it("renders the page for the right role", () => {
    renderAt("/admin", { role: "admin" });
    expect(screen.getByText("admin page")).toBeInTheDocument();
  });

  it("sends a signed-in non-admin to their own landing page, not to login", () => {
    // Bouncing a signed-in employee to /login reads as "you were logged
    // out", which is both wrong and alarming.
    renderAt("/admin", { role: "employee" });
    expect(screen.getByText("chat page")).toBeInTheDocument();
  });

  it("sends a signed-out visitor to login", () => {
    renderAt("/admin", null, "signed-out");
    expect(screen.getByText("login page")).toBeInTheDocument();
  });

  it("renders nothing while the boot refresh is still in flight", () => {
    // Deciding before the refresh resolves would redirect every reload of
    // an admin page to /chat for a moment.
    renderAt("/admin", null, "loading");
    expect(screen.queryByText("admin page")).not.toBeInTheDocument();
    expect(screen.queryByText("login page")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 6: Run it, watch it fail, then implement**

Create `frontend/src/auth/RequireRole.tsx`:

```tsx
import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { landingFor, useAuth } from "./AuthContext";

export function RequireRole({ role, children }: { role?: string; children: ReactNode }) {
  const { status, principal } = useAuth();
  if (status === "loading") return null;
  if (status === "signed-out" || !principal) return <Navigate to="/login" replace />;
  if (role && principal.role !== role) return <Navigate to={landingFor(principal)} replace />;
  return <>{children}</>;
}
```

Run: `cd frontend && npx vitest run src/auth/RequireRole.test.tsx` — 4 passed.

- [ ] **Step 7: Write the failing login-page test**

Create `frontend/src/pages/Login.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import * as ctx from "../auth/AuthContext";
import { Login } from "./Login";

function setup(login = vi.fn(), loginAsGuest = vi.fn()) {
  vi.spyOn(ctx, "useAuth").mockReturnValue({
    status: "signed-out", principal: null, login, loginAsGuest, logout: vi.fn(),
  } as never);
  render(<MemoryRouter><Login /></MemoryRouter>);
  return { login, loginAsGuest };
}

describe("Login", () => {
  it("signs in with username and password", async () => {
    const { login } = setup(vi.fn().mockResolvedValue({ role: "admin" }));
    await userEvent.type(screen.getByLabelText(/username/i), "admin");
    await userEvent.type(screen.getByLabelText(/password/i), "admin");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(login).toHaveBeenCalledWith("admin", "admin");
  });

  it("shows the server's message on bad credentials and keeps the form usable", async () => {
    setup(vi.fn().mockRejectedValue(new ApiError(401, "Invalid username or password")));
    await userEvent.type(screen.getByLabelText(/username/i), "admin");
    await userEvent.type(screen.getByLabelText(/password/i), "wrong");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(await screen.findByText(/invalid username or password/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeEnabled();
  });

  it("takes a name and email on the guest tab", async () => {
    const { loginAsGuest } = setup(vi.fn(), vi.fn().mockResolvedValue({ role: "guest" }));
    await userEvent.click(screen.getByRole("tab", { name: /guest/i }));
    await userEvent.type(screen.getByLabelText(/name/i), "Dana");
    await userEvent.type(screen.getByLabelText(/email/i), "dana@example.com");
    await userEvent.click(screen.getByRole("button", { name: /continue as guest/i }));
    expect(loginAsGuest).toHaveBeenCalledWith("Dana", "dana@example.com");
  });

  it("warns a guest that their session will not survive a reload", async () => {
    // Guests get no refresh cookie, so this is real behaviour, not a
    // hypothetical -- saying so up front beats a silent logout later.
    setup();
    await userEvent.click(screen.getByRole("tab", { name: /guest/i }));
    expect(screen.getByText(/will not survive|ends when you close/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 8: Run it, watch it fail, then implement `Login.tsx`**

Two tab buttons with `role="tab"`, a credentials form and a guest form. On success, `navigate(landingFor(principal), {replace: true})`. On failure, render `error.detail` in a `role="alert"` and re-enable the submit button. The guest panel carries the sentence "This session ends when you close or reload the tab." Submit buttons disable only while a request is in flight.

Run: `cd frontend && npx vitest run src/pages/Login.test.tsx` — 4 passed.

- [ ] **Step 9: Wire the router and the shell**

`frontend/src/main.tsx` wraps `<App/>` in `QueryClientProvider` → `BrowserRouter` → `AuthProvider`.

`frontend/src/App.tsx` declares the routes of spec §5: `/login` public; `/chat` and `/tickets` behind `<RequireRole>`; `/admin/*` behind `<RequireRole role="admin">`; `/` redirects to `landingFor(principal)` or `/login`; `*` renders a not-found.

`NavBar` shows the signed-in username, a Sign out button, links to Chat and Tickets, and — for admins only — the nine admin links. `StateBlock` renders the three states of spec §6.5 (`loading` / `empty` / `error`) and is used by every list screen from here on.

- [ ] **Step 10: Verify the whole suite and the type check**

Run: `cd frontend && npm test && npx tsc --noEmit`
Expected: all green.

- [ ] **Step 11: Commit**

```bash
git add frontend/src
git commit -m "Add sign-in, guest entry, role routing and the app shell"
```

---

## Task 3: Notifications bell and the stream hook

**Files:**
- Create: `frontend/src/api/endpoints/notifications.ts`, `frontend/src/hooks/useNotifications.ts`, `frontend/src/components/NotificationBell.tsx`, `frontend/src/hooks/useNotifications.test.tsx`
- Modify: `frontend/src/components/NavBar.tsx`

**Interfaces:**
- Consumes: `apiFetch`, `apiStream` (task 1); `useAuth` (task 2).
- Produces: `useNotifications(): {items: Notification[], unread: number, markRead(id)}`; `Notification` = `components["schemas"]["NotificationResponse"]`.

- [ ] **Step 1: Write the failing hook tests**

Create `frontend/src/hooks/useNotifications.test.tsx` covering exactly four behaviours:

```tsx
// 1. It never opens the stream for a guest.
//    The endpoint 403s guests (notifications.user_id is NOT NULL and a
//    guest is not a row in `users`), so opening it produces a guaranteed
//    error and a reconnect loop against a wall.
it("does not open the stream for a guest principal", async () => { /* assert no fetch to /stream */ });

// 2. A frame arriving on the stream is prepended and bumps the unread count.
it("prepends a streamed notification", async () => { /* ... */ });

// 3. An id present in both the backlog and the stream appears once.
//    The backend subscribes before reading its backlog so nothing is lost;
//    the overlap that produces is the client's to collapse.
it("does not duplicate a notification present in both backlog and stream", async () => { /* ... */ });

// 4. markRead decrements the unread count without refetching everything.
it("marks one notification read", async () => { /* ... */ });
```

Write each of these out in full with MSW handlers before implementing. Use MSW's `http.get("*/api/notifications/stream", ...)` returning a `ReadableStream` to simulate frames.

- [ ] **Step 2: Run, watch fail, implement**

`useNotifications` fetches `GET /api/notifications` through TanStack Query, then — only when `principal.kind === "user"` — opens `apiStream("/api/notifications/stream")` and feeds `readSse` frames into local state keyed by `id`, deduplicating against the query's data. Reconnect with exponential backoff capped at 30s; abort on unmount via `AbortController` passed as `signal`.

- [ ] **Step 3: Run and watch it pass. Add the bell to `NavBar`**

The bell shows the unread count and opens a dropdown listing title, body and relative time, each linking to `link_type`/`link_id` when present.

- [ ] **Step 4: Commit**

```bash
git add frontend/src
git commit -m "Add the notification bell over the live stream"
```

---

## Task 4: Chat

**Files:**
- Create: `frontend/src/api/endpoints/chat.ts`, `frontend/src/hooks/useChatTurn.ts`, `frontend/src/hooks/turnReducer.ts`, `frontend/src/pages/Chat.tsx`, `frontend/src/hooks/turnReducer.test.ts`, `frontend/src/pages/Chat.test.tsx`

**Interfaces:**
- Consumes: `apiFetch`, `apiStream`, `readSse` (task 1); `useAuth` (task 2).
- Produces: `turnReducer(state: TurnState, frame: TurnFrame): TurnState`; `TurnState {text: string, tools: ToolRow[], outcomes: Outcome[], error: string | null, runId: string | null, done: boolean}`; `useChatTurn(conversationId)`.

- [ ] **Step 1: Write the failing reducer tests**

The nine event types are fixed by `backend/app/agent/loop.py`. Create `frontend/src/hooks/turnReducer.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { emptyTurn, turnReducer } from "./turnReducer";

const apply = (...frames: Record<string, unknown>[]) => frames.reduce(turnReducer, emptyTurn());

describe("turnReducer", () => {
  it("accumulates token frames into one message", () => {
    expect(apply({ type: "token", text: "Hel" }, { type: "token", text: "lo" }).text).toBe("Hello");
  });

  it("pairs tool_start with tool_end by id", () => {
    const state = apply(
      { type: "tool_start", name: "search_knowledge", id: "t1" },
      { type: "tool_start", name: "lookup_employee", id: "t2" },
      { type: "tool_end", name: "search_knowledge", id: "t1", is_error: false },
    );
    expect(state.tools).toEqual([
      { id: "t1", name: "search_knowledge", status: "ok" },
      { id: "t2", name: "lookup_employee", status: "running" },
    ]);
  });

  it("marks a failed tool as failed, not as finished", () => {
    const state = apply(
      { type: "tool_start", name: "send_email", id: "t1" },
      { type: "tool_end", name: "send_email", id: "t1", is_error: true },
    );
    expect(state.tools[0].status).toBe("error");
  });

  it("collects the four outcome events as cards", () => {
    const state = apply(
      { type: "ticket_created", ticket_number: "TCK-000012", ticket_id: "abc" },
      { type: "approval_requested", request_number: "REQ-000003" },
      { type: "task_recorded", title: "VPN failure" },
      { type: "attachment_request", reason: "need a screenshot" },
    );
    expect(state.outcomes.map((o) => o.type)).toEqual([
      "ticket_created", "approval_requested", "task_recorded", "attachment_request",
    ]);
    expect(state.outcomes[0].data.ticket_number).toBe("TCK-000012");
  });

  it("keeps the text already streamed when an error arrives", () => {
    // The backend emits `error` mid-turn (budget exceeded, refusal). The
    // partial answer stays on screen: discarding it loses work the user
    // already read.
    const state = apply({ type: "token", text: "Partial" }, { type: "error", message: "Turn ended: budget." });
    expect(state.text).toBe("Partial");
    expect(state.error).toBe("Turn ended: budget.");
  });

  it("captures run_id from done, which is the link to the trace", () => {
    const state = apply({ type: "done", run_id: "r-1" });
    expect(state).toMatchObject({ runId: "r-1", done: true });
  });

  it("ignores an unknown event type instead of throwing", () => {
    // A backend that grows a tenth event type must not blank the chat page.
    expect(() => apply({ type: "something_new" })).not.toThrow();
  });
});
```

- [ ] **Step 2: Run, watch fail, implement `turnReducer.ts`, run again**

Expected: 7 passed.

- [ ] **Step 3: Write the failing Chat page tests**

Cover, with MSW: the conversation list renders from `GET /api/conversations`; **selecting a conversation renders its stored transcript from `GET /api/conversations/{id}`** (the task-0 field — this is what makes a reload survivable); sending a message streams tokens into a growing assistant bubble; a `ticket_created` frame renders a card linking to the ticket; an admin sees a "view trace" link after `done`, a non-admin does not; the composer is disabled while a turn is in flight.

- [ ] **Step 4: Run, watch fail, implement `Chat.tsx` and `useChatTurn.ts`, run again**

`useChatTurn` POSTs through `apiStream`, feeds `readSse` into `turnReducer`, and on `done` invalidates the conversation query so the stored transcript replaces the streamed one. A turn is never retried (spec §6.3).

Attachment upload posts to `POST /api/conversations/{id}/attachments` as `FormData` — `buildInit` already leaves `Content-Type` unset for `FormData` so the browser sets the multipart boundary. A 413 renders the server's size-limit message.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "Add the chat page with live turn streaming and stored transcripts"
```

---

## Task 5: Tickets

**Files:**
- Create: `frontend/src/api/endpoints/tickets.ts`, `frontend/src/pages/Tickets.tsx`, `frontend/src/components/{Table,Modal}.tsx`, `frontend/src/pages/Tickets.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` (task 1); `useAuth`, `StateBlock`, `Badge` (task 2).
- Produces: `listTickets(status?)`, `getTicket(id)`, `updateTicket(id, patch)`, `resolveTicket(id, resolution)`; `<Table>`, `<Modal>`.

- [ ] **Step 1: Write the failing tests**

Cover: the list renders scoped tickets with number, title, status badge, priority and assignee; a status filter re-queries with `?status=`; **an employee sees no edit controls and no Resolve button** (the server enforces it, but showing a button that always 403s is a lie about what the user can do); a helpdesk user sees both; resolving requires non-empty resolution text before the button enables; a failed PATCH renders `ApiError.detail` and leaves the row unchanged.

- [ ] **Step 2: Run, watch fail, implement, run again**

`PATCH` must never send `status: "resolved"` — the backend rejects it, because resolving without a resolution is what `POST /resolve` exists to prevent. Omit `resolved` from the status dropdown entirely and route it through the Resolve modal.

- [ ] **Step 3: Commit**

```bash
git add frontend/src
git commit -m "Add the tickets page with staff edit and resolve"
```

---

## Task 6: Admin Overview and Costs

**Files:**
- Create: `frontend/src/api/endpoints/admin.ts`, `frontend/src/hooks/useRunStream.ts`, `frontend/src/pages/Admin/Overview.tsx`, `frontend/src/pages/Admin/Costs.tsx`, plus their tests

**Interfaces:**
- Consumes: `apiFetch`, `apiStream`, `readSse`; `usd`, `tokens`, `duration`.
- Produces: `adminOverview()`, `adminCosts()`, `adminRuns({limit, offset})`, `adminTrace(runId)`, `adminConversations({q, limit, offset})`, `adminConversationDetail(id)`, `adminAudit(filters)`, `adminUsers()`, `patchUser()`, `adminLessons()`, `patchLesson()`, `archiveLesson()`, `adminApprovals(status?)`, `decideApproval()`, `buildDossier(ticketId)`; `useRunStream(): {events: RunEvent[], connected: boolean}`.

- [ ] **Step 1: Write the failing tests**

Overview: five counters render from `GET /api/admin/overview`; the activity feed appends a run frame from the stream; **an SSE disconnect triggers a refetch of the counters** (the backend drops slow subscribers rather than queueing, so a reconnect must re-read rather than assume continuity); `error_rate` renders as a percentage labelled "of today's completed runs", matching what the field means.

Costs: the four groupings each render a table; totals render with `cache_hit_rate` as a percentage; a `by_model` row whose `cost_usd` is null renders "unpriced"; the by-day bar widths are proportional to the largest day.

- [ ] **Step 2: Run, watch fail, implement, run again**

`useRunStream` mirrors `useNotifications`' reconnect policy but exposes `connected` so Overview and Traces can both show a live/reconnecting indicator and refetch on reconnect.

- [ ] **Step 3: Commit**

```bash
git add frontend/src
git commit -m "Add the admin overview and costs screens"
```

---

## Task 7: Admin Traces and the waterfall

**Files:**
- Create: `frontend/src/pages/Admin/Traces.tsx`, `frontend/src/components/SpanTree.tsx`, `frontend/src/components/SpanTree.test.tsx`, `frontend/src/pages/Admin/Traces.test.tsx`

**Interfaces:**
- Consumes: `adminRuns`, `adminTrace`, `useRunStream`, `usd`, `tokens`, `duration`.
- Produces: `<SpanTree roots={SpanNode[]} totalMs={number} />`, reused by Task 8's Conversations detail.

- [ ] **Step 1: Write the failing SpanTree tests**

```tsx
// Fixtures are shaped exactly like RunTrace.roots from the API.
it("renders nested children indented under their parent");
it("renders a duration bar proportional to the run total");
it("renders 'unpriced' for a null cost_usd and never $0.00");
it("collapses and expands a node's redacted input/output");
it("renders a span with null duration_ms without breaking the bar layout");
it("renders a deep tree without exceeding the stack"); // 200 nested nodes
```

- [ ] **Step 2: Run, watch fail, implement `SpanTree.tsx`, run again**

Recursive component over `SpanNode`. Each row: kind, name, proportional bar, model, four token counts, cost, status. Expanding reveals `input`/`output` in a `<JsonBlock>`. **The data is already redacted at persistence time — the component must not claim to redact anything.**

- [ ] **Step 3: Write the failing Traces page tests**

The list renders id, trigger, status, started, duration, cost, llm/tool call counts; selecting a row loads its trace; **`truncated: true` renders a visible banner saying spans were dropped at the cap** (a silently short waterfall reads as a run that stopped); a new run arriving on the stream prepends to the list; a run with `status: "error"` shows its `error` text.

- [ ] **Step 4: Run, watch fail, implement `Traces.tsx`, run again. Commit**

```bash
git add frontend/src
git commit -m "Add the traces screen with the collapsible span waterfall"
```

---

## Task 8: Admin Conversations

**Files:**
- Create: `frontend/src/pages/Admin/Conversations.tsx`, `frontend/src/pages/Admin/Conversations.test.tsx`

**Interfaces:**
- Consumes: `adminConversations`, `adminConversationDetail`, `adminTrace`, `<SpanTree>` (task 7).

- [ ] **Step 1: Write the failing tests**

The list renders title, participant (username or guest name/email), status and created date; the search box re-queries with `?q=`; **the detail renders the transcript beside the span tree of the selected run** (parent spec §15's requirement, and the reason task 0 exists); a conversation with several runs renders one selectable row per run; a conversation with no runs renders an explicit "no runs recorded" rather than an empty panel.

- [ ] **Step 2: Run, watch fail, implement, run again. Commit**

Two-column layout: transcript left, run list plus `<SpanTree>` right.

```bash
git add frontend/src
git commit -m "Add the admin conversations screen with transcript and span tree"
```

---

## Task 9: Admin Approvals

**Files:**
- Create: `frontend/src/pages/Admin/Approvals.tsx`, `frontend/src/pages/Admin/Approvals.test.tsx`

- [ ] **Step 1: Write the failing tests**

The pending queue renders request number, action type, risk badge, justification, agent summary and the full payload; a link goes to the source conversation; approve and deny both send `POST /decide` with `{approve, note}`; **the decide buttons disable while the decision is in flight** (the backend's idempotency guard is a row lock — a double-click would have sent two real emails before Phase 6 fixed it, and not resending is still the client's job); a decided item stays visible with its `execution_result`; a `?status=` filter switches between pending and decided.

- [ ] **Step 2: Run, watch fail, implement, run again. Commit**

```bash
git add frontend/src
git commit -m "Add the admin approvals queue with decisions and results"
```

---

## Task 10: Admin Tickets board and the dossier

**Files:**
- Create: `frontend/src/pages/Admin/Tickets.tsx`, `frontend/src/components/DossierCard.tsx`, and their tests

- [ ] **Step 1: Write the failing tests**

The board renders one column per `TicketStatus` with its tickets; each card shows assignee, matched specialization, rationale and score; **Generate dossier enters a pending state and stays pending for a long call** (36.5s measured in Phase 8a — no client-side timeout shorter than the server's); a returned dossier renders all fifteen sections; Download JSON produces the dossier verbatim; **a failure renders the server's error and no partial card** (it is schema-validated, so half a dossier means a bug, not a degraded result).

- [ ] **Step 2: Run, watch fail, implement, run again. Commit**

```bash
git add frontend/src
git commit -m "Add the admin ticket board and the incident dossier card"
```

---

## Task 11: Admin Users, Lessons and Audit

**Files:**
- Create: `frontend/src/pages/Admin/{Users,Lessons,Audit}.tsx`, `frontend/src/components/Pager.tsx`, and their tests

- [ ] **Step 1: Write the failing tests**

Users: the paginated table renders username, full name, email, role, clearance, department, refs and a dev-seed badge; editing role or clearance sends `PATCH` and re-renders from the response; a 126-row dataset pages correctly; the pager respects `total` and the server's clamped `limit`.

Lessons: the table renders title, category, status, confidence and source ticket; editing `content_md` sends `PATCH`; **Archive sends `DELETE` and the row stays visible with an "archived" badge** (the backend archives rather than deletes, so a row vanishing from the table would misrepresent what happened); archiving twice is not an error.

Audit: the table renders actor, action, target, payload and timestamp; the actor/action/target/date filters each re-query; the payload renders in a `<JsonBlock>`; an empty result renders "no matching entries" rather than a blank table.

- [ ] **Step 2: Run, watch fail, implement, run again. Commit**

```bash
git add frontend/src
git commit -m "Add the admin users, lessons and audit screens"
```

---

## Task 12: The phase gate — Playwright against the real stack

**Files:**
- Create: `frontend/playwright.config.ts`, `frontend/tests/e2e/{auth,screens,guest}.spec.ts`, `frontend/tests/e2e/fixtures.ts`
- Modify: `README.md`, `frontend/package.json`

**Interfaces:**
- Consumes: a running backend on `:8000` with a migrated, seeded database; the dev server on `:5173`.

- [ ] **Step 1: Install Playwright**

```bash
cd frontend && npm install -D @playwright/test && npx playwright install chromium
```

`playwright.config.ts` sets `baseURL: "http://localhost:5173"` and a `webServer` running `npm run dev`. It does **not** start the backend — a gate that silently starts its own backend can pass against an empty database.

- [ ] **Step 2: Bring the real stack up**

```bash
docker start postgres18 chroma
cd backend && uv run python tasks.py db-up && uv run python tasks.py migrate && uv run python tasks.py seed
```

Then start the backend: `cd backend && uv run python tasks.py dev`.

Confirm seeding: the users endpoint must report 126 accounts. If any of this cannot be brought up, **the gate is reported as not run** — never as passed (spec §7.1).

- [ ] **Step 3: Write the gate specs**

`auth.spec.ts`:

```ts
test("admin signs in and reaches the admin panel", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel(/username/i).fill("admin");
  await page.getByLabel(/password/i).fill("admin");
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByText(/runs today/i)).toBeVisible();
});

test("a seeded employee signs in and reaches chat", async ({ page }) => { /* ... */ });

test("a signed-in session survives a page reload", async ({ page }) => {
  // The memory-only access token plus the httpOnly refresh cookie, proven
  // cross-origin rather than assumed.
  await signInAsAdmin(page);
  await page.reload();
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByText(/runs today/i)).toBeVisible();
});

test("a non-admin is redirected away from /admin", async ({ page }) => { /* ... */ });
```

`screens.spec.ts` — **the parent spec's phase 8 gate**. One test per route, each asserting a screen-specific element and that the page made no failed API call:

```ts
const SCREENS = [
  ["/admin", /runs today/i],
  ["/admin/conversations", /participant/i],
  ["/admin/traces", /duration/i],
  ["/admin/approvals", /risk/i],
  ["/admin/tickets", /generate dossier|no tickets/i],
  ["/admin/users", /clearance/i],
  ["/admin/lessons", /confidence/i],
  ["/admin/audit", /actor/i],
  ["/admin/costs", /cache hit rate/i],
  ["/chat", /send/i],
  ["/tickets", /ticket/i],
] as const;

for (const [path, marker] of SCREENS) {
  test(`${path} renders against seeded data`, async ({ page }) => {
    const failures: string[] = [];
    page.on("response", (r) => {
      if (r.url().includes("/api/") && r.status() >= 400) failures.push(`${r.status()} ${r.url()}`);
    });
    await signInAsAdmin(page);
    await page.goto(path);
    await expect(page.getByText(marker)).toBeVisible();
    expect(failures).toEqual([]);
  });
}
```

The `failures` assertion is what makes this a real gate: a screen that renders its empty state because every call 500'd would otherwise pass.

`guest.spec.ts`: a guest signs in, reaches `/chat`, and **never requests `/api/notifications/stream`** — asserted on the network, and needing no API key since no turn is sent.

- [ ] **Step 4: Run the gate**

Run: `cd frontend && npx playwright test`
Expected: all pass. Any failure is a real defect in a screen — fix the screen, not the assertion.

- [ ] **Step 5: Prove the gate can fail**

Per `feedback_break_the_code_to_prove_coverage`: temporarily break one screen (delete the `runs_today` counter from `Overview.tsx`), re-run, and confirm the corresponding test fails. Then restore it and re-run. **A gate never seen to fail is not known to be a gate.**

- [ ] **Step 6: Run everything**

```bash
cd frontend && npm test && npx tsc --noEmit && npm run api:check && npm run build
cd ../backend && uv run python tasks.py test
```

Expected: frontend suite green, type check clean, no OpenAPI drift, build succeeds, backend suite at 744 + the new task-0 tests, 0 failed.

- [ ] **Step 7: Document it**

Add a `## Frontend` section to `README.md`: install, `.env` setup, `npm run dev`, the required backend/Postgres/Chroma processes, how to run each test layer, and how to regenerate the API types. Record the measured gate result — the number of screens verified and anything that could not be verified, stated as unverified rather than omitted.

- [ ] **Step 8: Commit**

```bash
git add frontend README.md
git commit -m "Add the phase 8b gate: every screen rendered against seeded data"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §4.1 transcript on `GET /conversations/{id}` | 0 |
| §4.2 `GET /admin/conversations/{id}` | 0 |
| §4.3 four typed responses | 0 |
| §4.4 OpenAPI dump script | 0 |
| §3 file structure, D2 generated types, D8 drift check | 1 |
| D1 / §6.3 SSE reader | 1 |
| §6.2 transport and refresh mutex | 1 |
| §6.1 auth, memory-only token, guest reload | 2 |
| §5 routes, role landing, D5 primitives | 2 |
| §6.3 notification stream, guest exclusion | 3 |
| §6.4 chat turn, nine event types | 4 |
| §5 tickets | 5 |
| §5 overview, §5.3 costs | 6 |
| §5.1 waterfall, unpriced, truncation | 7 |
| §5 conversations detail | 8 |
| §5 approvals | 9 |
| §5 admin tickets, §5.2 dossier | 10 |
| §5 users, lessons, audit | 11 |
| §7 all three test layers, §7.1 honest gate reporting | 1–12 |
| §6.5 loading/empty/error states | 2 (`StateBlock`), used by 5–11 |

No spec section is unclaimed.

**Type consistency:** `MessageView` is defined once in `backend/app/chat/schemas.py` and referenced by both `ConversationResponse` and `ConversationDetail`, which task 0's `test_message_view_is_shared_not_duplicated` enforces at the schema level so the generated TypeScript has one type, not two. `SpanNode` comes from the generated schema in tasks 7 and 8. `RunSummary` is built by the single extracted `_run_list_row` in both `admin_runs` and `admin_conversation_detail`. `apiFetch`/`apiStream`/`readSse` keep the same signatures from task 1 through task 12.

**Placeholder scan:** Tasks 3, 5, 6, 8, 9, 10 and 11 specify their tests as an explicit list of named behaviours with the reason each one exists, rather than as code blocks. That is deliberate for the repetitive table screens — the behaviours are named precisely enough to write directly, and the non-obvious ones (guest stream exclusion, PATCH never sending `resolved`, archive-not-delete, refetch on SSE reconnect, decide-button disabling) each carry the reason they matter. The load-bearing modules — the SSE reader, the refresh mutex, the turn reducer, the auth context, the route guard, the formatters, and every backend change — carry complete code and complete tests.
