# Phase 8a — Admin API and Dossier

**Date:** 2026-08-29
**Parent spec:** `2026-08-24-agentic-helpdesk-design.md` §14, §15, §15.1, §17, §18
**Gate:** Every admin endpoint returns correct data against real data, enforces admin-only access, and audits every mutation; the dossier validates against its schema on a live call.

---

## 1. Why this phase is split

Parent spec §18's phase 8 is "Admin panel (all screens + dossier)". That is two
independent subsystems: roughly ten backend endpoints, and an entire frontend
application (Vite + React + Tailwind, login plus nine admin screens). Each is
large enough to need its own spec, plan, and review cycle, and the backend one
produces working, testable software on its own.

- **Phase 8a (this spec):** the admin API and the dossier.
- **Phase 8b (next):** the admin panel frontend, consuming 8a.

## 2. Starting position

`app/admin/router.py` exists with exactly two endpoints from Phase 6:
`GET /api/admin/approvals` and `POST /api/admin/approvals/{id}/decide`. Both are
already behind `require_role("admin")` and the decide path already writes an
audit row. Everything else in §14's admin list is missing.

Available to build on:

- `app/tracing/store.py::trace_tree(run_id) -> RunTrace` already reconstructs a
  full span tree ordered by `sequence`.
- `app/notifications/broker.py` is a generic UUID-keyed in-process pub/sub,
  thread-safe across the commit path, with the `SubscriberDropped` contract
  hardened in Phase 7.
- `app/audit/service.py::record_audit` stages and flushes without committing.
- `RunTrigger.DOSSIER` already exists in the enum.
- `anthropic` 1.0.0 provides `client.messages.parse` (verified).

## 3. What the data actually looks like

Measured on the development database, and it matters for the gate:

| Table | Rows |
|---|---|
| `runs` | 521 |
| `spans` | 20,348 |
| `users` | 126 |
| `tickets` | 0 |
| `lessons` | 0 |
| `audit_log` | 0 |

So "every screen renders against seeded data" (§18) cannot mean what it says for
tickets, lessons, and audit: tickets come from agent runs, lessons from Phase 9's
learning loop, and audit rows from admin mutations. **This phase designs for
correct empty states and reports them as empty rather than fabricating rows.**
The audit table stops being empty as soon as this phase's own mutating endpoints
are exercised, which the tests do.

## 4. Endpoints

All are behind `require_role("admin")`. Every mutating call writes an
`audit_log` row in the same transaction as the mutation, so an audit entry can
never survive a change that was rolled back.

| Endpoint | Purpose |
|---|---|
| `GET /api/admin/overview` | Runs today, spend today, pending approvals, open tickets, error rate |
| `GET /api/admin/conversations` | Paginated, searchable by title and participant |
| `GET /api/admin/runs` | Paginated; cost, latency, status, trigger |
| `GET /api/admin/runs/{id}/trace` | The span tree via `trace_tree` |
| `GET /api/admin/runs/stream` | SSE; live run activity |
| `GET /api/admin/users` | Paginated; role, clearance, dev-seed flag |
| `PATCH /api/admin/users/{id}` | Role and clearance only; audited |
| `GET /api/admin/lessons` | Paginated |
| `PATCH /api/admin/lessons/{id}` | Content and status; audited |
| `DELETE /api/admin/lessons/{id}` | Archives rather than deletes; audited |
| `GET /api/admin/audit` | Filterable by actor, action, target type, date range |
| `GET /api/admin/costs` | By day, model, user, trigger; token totals; cache hit rate |
| `POST /api/admin/tickets/{id}/dossier` | §15.1 |

### 4.1 Pagination is mandatory

Every list endpoint takes `limit` and `offset`, with a **hard server-side cap of
200** and a default of 50. This is not defensive decoration: there are already
20,348 spans and 521 runs, so an unbounded list endpoint would be both a
production hazard and a slow test. A `limit` above the cap is clamped, not
rejected — a client asking for too much gets the maximum rather than an error.

Every list response carries `{items, total, limit, offset}` so the frontend can
paginate without a second count request.

### 4.2 `DELETE /api/admin/lessons/{id}` archives

`LessonStatus` has an `archived` value, and parent spec §20 says lessons are
"admin-reviewable and archivable" precisely so a bad lesson can be withdrawn
without destroying the record of it having existed. The DELETE verb therefore
sets `status = archived`; it does not remove the row. The response says so, and
the audit row records `lesson.archived`, not `lesson.deleted`.

## 5. The runs SSE stream

`GET /api/admin/runs/stream` reuses `notifications/broker.py` rather than
introducing a second pub/sub. The broker is already keyed by UUID, already
thread-safe across the synchronous commit path, and already carries the
`SubscriberDropped` contract that Phase 7 hardened. A module-level constant
`ADMIN_RUNS_CHANNEL` (a fixed sentinel UUID) is the channel every admin
subscribes to.

Writing a parallel broker would mean rediscovering the same three defects Phase 6
and Phase 7 already paid for: cross-thread queue access, a dropped subscriber
hanging forever, and a lossy window between reading the backlog and subscribing.

The endpoint follows the shape those phases arrived at:

- **Subscribe before reading any backlog**, so nothing published in between is lost.
- **Release the database session before entering the keepalive loop.** Phase 6
  measured that an SSE endpoint holding its request session pins a pooled
  Postgres connection `idle in transaction` for the life of the connection, and
  fifteen such streams exhaust the pool.
- **Catch `SubscriberDropped` and close the stream**, so a client that fell
  behind reconnects and replays rather than hanging silently.
- Keepalive comment every 15 seconds.

`tracing/store.py` publishes a compact run event on run finalisation. Publishing
happens on the tracing side because that is where a run's terminal state is
known.

## 6. The dossier

`POST /api/admin/tickets/{id}/dossier` runs a traced Claude call using
`client.messages.parse` against the `IncidentDossier` model from §15.1, inside a
run with `RunTrigger.DOSSIER`.

**Schema validation is the whole point.** A dossier that does not validate is an
error, not a plausible-looking fabrication — that is why §15.1 specifies
`messages.parse` rather than free-text generation followed by hopeful parsing. A
`ValidationError` therefore surfaces as a failed dossier with the reason
recorded, never as a partial object.

The call is given: the ticket and its task, the conversation transcript, the
spans of the run that classified it, and the run's cost summary. It is assembled
in `app/admin/dossier.py`, which owns the model definitions and the prompt and
knows nothing about HTTP.

The transcript is untrusted content — it contains whatever a user typed and
whatever an attachment said. It is wrapped with
`guardrails.wrap_untrusted(source="conversation/<id>")` before reaching the
model, on the same boundary Phase 7 hardened.

Response: the validated dossier as JSON. Rendering it as a card and offering it
as a download is Phase 8b's job.

## 7. Module layout

```
app/admin/   router.py (extend)  queries.py (new)  dossier.py (new)
```

- **`router.py`** is HTTP: authorization, pagination parameters, serialisation.
- **`queries.py`** owns the read aggregations — overview counters, cost
  roll-ups, filtered audit and conversation lists. Pure query functions over a
  `Session`, so they can be tested without HTTP.
- **`dossier.py`** owns the `IncidentDossier` models, the prompt, and the traced
  call. It knows nothing about HTTP or about `admin/router.py`.

Splitting the aggregations out of the router matters here: nine screens' worth of
queries in a router file is exactly the shape that becomes unreviewable, and this
project has already seen the value of keeping units small enough to hold in
context at once.

## 8. Testing

**Unit** — pagination clamping at the cap and at zero/negative input; cost
aggregation arithmetic against known rows; overview counters including the
"today" boundary; audit filter combinations; the dossier prompt assembling all
required sections.

**Integration** — every endpoint returns correct data against the real
development data; `PATCH /users/{id}` writes both the change and its audit row in
one transaction, and neither survives a rollback; `DELETE /lessons/{id}` archives
rather than deletes; the SSE stream delivers a run event to a live subscriber.

**Security** — every endpoint rejects a guest, an employee, and a helpdesk user
with 403; `PATCH /users/{id}` cannot change anything except role and clearance
(an attempt to alter `password_hash`, `username`, or `id` is ignored or
rejected, never applied); the dossier's transcript reaches the model only inside
an `<untrusted_data>` wrapper.

**Empty states** — tickets, lessons, and audit are empty at the start of this
phase. Every list endpoint over an empty table returns `{items: [], total: 0}`
with a 200, not a 404 and not an error.

**Live** — one marked, opt-in test proving a real dossier validates against the
schema. This is the only thing that proves the model can actually fill
`IncidentDossier`; the offline tests use a stubbed client and prove only the
assembly and error handling around it. The phase report must cite the live run
for that claim and not the offline suite.

## 9. Known hazards carried into this phase

- **Never background a long test run.** Agents in this project have repeatedly
  stalled waiting for notifications that cannot reach them; the cause is the
  300-second default tool timeout and the remedy is an explicit longer timeout in
  the foreground.
- **`User.full_name` is NOT NULL with no default** — every `User(...)` in a test
  must set it.
- **Committed rows leak.** Anything inserted through an independently-committing
  session is not rolled back by `db_session` at teardown, and a leaked `users`
  row breaks `test_seed.py`'s exact-count assertion for everyone. Tests that must
  hard-commit follow the sweep pattern in `tests/test_approvals_service.py`.
- **`func.now()` is transaction-start time**, so rows created in one transaction
  share a timestamp and any ordering that falls through to a random UUID
  tiebreaker is effectively random. This has already been fixed twice in this
  project; set timestamps explicitly where ordering matters.
- **A list endpoint holding a session across a stream** pins a pooled connection.
  See §5.
