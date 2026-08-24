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
cd backend && uv run python tasks.py <db-up|db-create|migrate|seed|dev|test>
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
- `make dev` — run the FastAPI dev server on `$BACKEND_HOST:$BACKEND_PORT`
  (from `.env`, default `127.0.0.1:8080`)
- `make test` — run the backend test suite

### Port already in use?

`BACKEND_PORT` in `.env` controls the port `make dev` binds to — change it
there if the default is occupied by something else on your machine (no
code edit needed).
