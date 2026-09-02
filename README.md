# Ticketing (Agentic Helpdesk)

Python/FastAPI backend for an agentic IT helpdesk. See
`docs/superpowers/specs/2026-08-24-agentic-helpdesk-design.md` for the full
design.

## Local environment

Dev services (Postgres 18, Chroma) are expected to already be running as
`postgres18` and `chroma` containers. `make db-up` health-checks them.

### No `make`?

`make` isn't installed on every machine. Every target below is a thin
wrapper around `backend/tasks.py`, a cross-platform Python runner with no
extra dependencies — use it directly:

```sh
cd backend && uv run python tasks.py <db-up|db-create|migrate|seed|dev|test|ingest|eval>
```

Anywhere this doc says `make <target>`, that's equivalent to
`cd backend && uv run python tasks.py <target>`.

### Clean-slate setup

If you tear the environment down (or are starting fresh) and want to bring
it back up from `docker-compose.yml`, run these in order:

```sh
docker compose up -d
make db-create   # creates the `ticketing` database (compose's default POSTGRES_DB is `mydb`)
make migrate     # applies Alembic migrations
make seed        # loads seed data
```

`db-create` is idempotent — safe to run again against an already-set-up
environment.

## Common tasks

- `make db-up` — verify Postgres and Chroma are reachable
- `make db-create` — create the `ticketing` database if it doesn't exist
- `make migrate` — run Alembic migrations (`backend/alembic`)
- `make seed` — load seed data
- `make ingest` — chunk and load `corporate_rag_dataset/` into Chroma (idempotent)
- `make eval` — run the retrieval evaluation gate (Recall@5/10, MRR, nDCG@10; must be run after `make ingest`)
- `make dev` — run the FastAPI dev server on `$BACKEND_HOST:$BACKEND_PORT`
  (from `.env`, default `127.0.0.1:8080`)
- `make test` — run the backend test suite

### Port already in use?

`BACKEND_PORT` in `.env` controls the port `make dev` binds to — change it
there if the default is occupied by something else on your machine (no
code edit needed).

## Phase 6: approvals, execution, and notifications

Two new endpoint groups:

- **`/api/admin/approvals*`** — `GET /api/admin/approvals` (optionally
  `?status=pending`) lists approval requests for the admin panel;
  `POST /api/admin/approvals/{id}/decide` records an approve/deny decision
  and, on approval, executes the action synchronously in the same request
  (bounded by the SMTP timeout on a `send_email` approval). Both routes
  require the `admin` role.
- **`/api/notifications/*`** — `GET /api/notifications` lists the caller's
  notifications; `POST /api/notifications/{id}/read` marks one read;
  `GET /api/notifications/stream` is an SSE feed of live notifications,
  with replay of missed events on reconnect.

### Outbound email configuration

Two settings gate every real send:

- `SMTP_SECURE` — `true` selects implicit TLS (`SMTP_SSL`), `false` selects
  STARTTLS. Port 465 always forces implicit TLS regardless of this flag (no
  server speaks STARTTLS there), but set it explicitly so the intent is
  legible.
- `EMAIL_RECIPIENT_ALLOWLIST` — a comma-separated list of exact addresses
  and/or `fnmatch`-style glob patterns (e.g. `*@northstar.example`). A
  recipient must match at least one entry or the send is rejected before a
  socket ever opens. **An empty allowlist rejects every recipient — it
  fails closed, not open.** A rejected send is still recorded in
  `outbound_emails` as `failed`, so the rejection itself is auditable.

### Simulated approval actions

Three of the seven approval action types have no real target in this
system — there is no external identity provider, credential store, or
external API to act against — and are therefore **simulated**:
`grant_system_access`, `reset_credential`, and `external_api_write`. Their
executor handlers record `{"simulated": true, ...}` in `execution_result`
instead of performing any real side effect. The other four action types
(`send_email`, `update_user_clearance`, `disclose_restricted_information`,
`cross_department_ticket_assignment`) act for real against this system's
own data.

### Notification broker is single-worker

The notification broker that backs `/api/notifications/stream` is
in-process (an `asyncio` pub/sub, not backed by Redis or Postgres
`LISTEN`/`NOTIFY`). It assumes a single worker process. Run multiple
uvicorn workers and a client connected to one worker will never see an
event published from another — the SSE stream's replay-on-reconnect keeps
the feed eventually correct (nothing is lost), it just is not instant
across workers.

## Phase 7: multimodal attachments

Two new endpoints:

- **`POST /api/conversations/{id}/attachments`** — uploads a file (image,
  PDF, or audio), stores it, and parses it synchronously through Gemini
  before responding. The response includes `parse_status` (`parsed` or
  `failed`) and, on failure, `parse_error`.
- **`GET /api/attachments/{id}`** — returns the raw bytes with the stored
  MIME type. Scoped to the conversation's owner (or an admin); no
  `Content-Disposition` is set, so download semantics are a frontend
  concern, not this endpoint's.

**Validation.** Only `png`/`jpg`/`jpeg`/`webp` (image), `pdf`, and
`mp3`/`wav`/`m4a`/`ogg` (audio) are accepted, capped at 20 MB. The declared
extension, the declared MIME type, and the file's actual magic bytes must
all agree — a PDF renamed to `.png` is refused even though both extensions
are individually allowed, because the mismatch itself is the signal.

**Storage.** Files live under `ATTACHMENT_STORAGE_DIR` (default
`storage/uploads/`, already covered by `.gitignore`), content-addressed by
SHA-256 and sharded by the digest's first byte
(`<dir>/<sha256[:2]>/<sha256>`). A duplicate upload — same bytes, same
conversation — reuses the existing extraction instead of paying for a
second Gemini call.

**Parsing.** Extraction happens synchronously inside the upload request, so
by the time the response comes back the extracted text is guaranteed to be
present for the next turn. A parse failure (no key, a transport error, or
Gemini returning no text) is recorded on the attachment row (`parse_status
= failed`, `parse_error` set) rather than losing the uploaded file or
failing the request with a 500.

**Reaching the agent.** A parsed attachment's text is injected into the
model's next turn wrapped in `<untrusted_data source="..." trust="none">`
— the same boundary RAG chunks go through (spec 12.1). Web-search results
do not go through this boundary: `web_search` is an Anthropic server tool
whose results are injected directly by the API, never passing through our
code. The agent never sees raw pixels, audio, or PDF bytes, only the text
Gemini extracted, and only as untrusted data a user-role content block can
carry — never as a system instruction. A file that appears to contain
instructions is still transcribed faithfully (the extraction is not
censored) and still flagged if it matches an injection heuristic, but it
can never act as one.

**No `GEMINI_API_KEY` configured.** Uploads are refused with `503` before
any file is stored, and the `request_attachment` tool disappears from the
agent's tool catalog entirely rather than being offered and then failing.
(FastAPI still parses and spools the full multipart body first, the same as
it does for every upload — the `503` is only guaranteed before our own
attachment store is touched.)

## Phase 8a: admin API and the incident dossier

Every screen the Phase 8b panel will render has an endpoint behind it. All
of them are under `/api/admin` and all of them require the `admin` role —
an employee, a helpdesk user, and a guest each get `403`, an anonymous
caller `401`.

| Endpoint | What it answers |
| --- | --- |
| `GET /overview` | Today's runs, spend, error rate, cache hit rate |
| `GET /costs` | Spend by day and by model |
| `GET /runs` | Run history |
| `GET /runs/{id}/trace` | One run's full span tree |
| `GET /runs/stream` | Live run activity (SSE) |
| `GET /conversations` | Conversations, searchable by participant |
| `GET /audit` | The audit log, filterable by action and date range |
| `GET /users` | User accounts |
| `PATCH /users/{id}` | Change a user's role, clearance, or active flag |
| `GET /lessons` | Learned lessons |
| `PATCH /lessons/{id}` | Correct a lesson's text, title, or status |
| `DELETE /lessons/{id}` | Archive a lesson |
| `GET /approvals`, `POST /approvals/{id}/decide` | Phase 6's approval queue |
| `POST /tickets/{id}/dossier` | Build an incident dossier |

**Pagination.** Every list endpoint takes `limit` and `offset` (default
`50`) and answers with `{items, total, limit, offset}` — `total` is the
count *before* the window, so a client can render a pager without walking
the whole result set. `limit` is capped at **200**, and an over-large value
is **clamped, not rejected**: asking for 10,000 gets you 200, not a `422`.
An over-large limit is a client bug, not an attack, and failing the request
helps nobody. The cap is not decoration — there are already tens of
thousands of spans and hundreds of runs in a development database.

**`GET /runs/{id}/trace` is capped at 500 spans** and answers with
`span_count` and a `truncated` flag. A trace is unbounded in the data — one
run's tree measured 167,617 bytes here — and a silently shortened waterfall
reads as a run that simply stopped. Spans are serialised depth-first, so a
capped trace is a correct prefix of the real one rather than every root
with none of its body.

**Every mutation is audited**, and the audit row is written in the *same
transaction* as the change it records. There is no window in which a role
change is visible but unaudited.

**`DELETE /lessons/{id}` archives, it does not delete.** A lesson is
evidence of what the system learned and why it behaved as it did; deleting
the row would remove the explanation for past behaviour while leaving that
behaviour in place. A second `DELETE` on an already-archived lesson returns
`200` with the same body rather than `409` — the verb states a desired end
state and that state already holds. The second audit row is still written:
an admin issued the request, which is true whether or not the row changed,
and suppressing it because the write was a no-op is how an audit trail
starts lying by omission.

**The live run stream is single-worker**, for the same reason as the
notification stream — it reuses that same in-process broker, subscribing
every admin to one fixed sentinel channel rather than standing up a second
pub/sub. `finalize_run` publishes *after* its commit, so a subscriber is
never told about a finalisation that then rolled back. The endpoint holds
no database session at all: an SSE response completes only when the client
disconnects, so a session it had read through would sit `idle in
transaction` for the life of the stream, and roughly fifteen such streams
exhaust the connection pool. There is no backlog to replay — run history
comes from `GET /runs`.

**The dossier is schema-validated.** `POST /tickets/{id}/dossier` runs a
traced Claude call through `client.messages.parse` against the
`IncidentDossier` model. A dossier that does not validate is an **error**,
not a plausible-looking fabrication, because an admin acts on what it says:
a schema violation, a transport failure, or a response carrying nothing
parsed all surface as `502` with the reason, never as a half-built object.
The conversation transcript reaches the model wrapped in
`<untrusted_data ... trust="none">` — it contains whatever a user typed and
whatever an attachment said — while the instructions live in the `system`
turn, so anything instruction-shaped in the user turn arrived with the data
and is not ours. The cost figures are read from the run row and overwrite
whatever the model returns: they are facts already held exactly, and a
transcription slip in a cost figure is indistinguishable from a real one
once it is rendered as a card.

**Empty tables are expected.** `tickets`, `lessons`, and `audit_log` start
empty on a fresh database and fill as the system is used, so the Tickets,
Lessons, and Audit screens are legitimately blank until something has
happened. `users` is seeded (126 rows); `runs` and `conversations` fill as
soon as anyone chats.

**Live dossier check.** One opt-in test makes a real, paid Anthropic call
and is excluded from the default run:

```bash
uv run pytest tests/test_admin_dossier_live.py -v -s -m live_dossier
```

It is the only test that proves a real model can fill the schema — every
other dossier test stubs the client and would stay green against a schema
no model could satisfy.
