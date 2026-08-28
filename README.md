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
