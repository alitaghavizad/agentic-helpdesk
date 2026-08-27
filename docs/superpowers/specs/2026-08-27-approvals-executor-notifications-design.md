# Phase 6 — Approvals, Executor, Notifications, Email

**Date:** 2026-08-27
**Parent spec:** `2026-08-24-agentic-helpdesk-design.md` §9, §10, §14, §16, §18, §19
**Gate (§18, phase 6):** Approve → execute → SSE + email; the "no email without approval" invariant test passes.

---

## 1. Starting position

Phase 1 already created every table and enum this phase needs: `approval_requests`,
`outbound_emails`, `notifications`, and the `ApprovalStatus`, `EmailStatus`, and
`NotificationType` enums. Phase 4 shipped the agent's `create_approval_request` tool and
`approvals.service.create()`, which files a request and deliberately stops there.

What does not exist: `decide()`, any executor, any email path, the notification broker and
its SSE endpoint, and the admin router. `app/admin/` and `app/notifications/` are new
packages.

## 2. Amendments to the parent spec

Four, each a deliberate correction rather than a drift:

**2.1 `executor.py` lives in `app/approvals/`, not `app/agent/`.** Layout §16 lists it
under `agent/`, but §9.2 says `approvals.execute()` dispatches to it, and Phase 4 built the
agent without it. It executes admin-approved actions, not agent tool calls.

**2.2 `RunTrigger` gains `approval_execution`.** §9.2 requires the executor to run inside
an `executor` span, which requires a run. The §5.4 enum has no value for an
admin-initiated execution. The alternative — labelling it `chat_turn` — would corrupt the
cost dashboard's per-trigger breakdown (§17).

**2.3 The `outbound_emails` invariant admits `failed`.** §5.3 states no `outbound_emails`
row may exist without an approval in `approved` or `executed`. A send that fails
legitimately leaves an email row behind and moves the approval to `failed`, so the
constraint must permit it. This widens the wording, not the intent: `failed` is reachable
only from `approved`, so an email row still proves the action passed approval.

**2.4 SMTP transport resolves both implicit TLS and STARTTLS.** §9.3 specifies STARTTLS.
The configured account (`smtp.gmail.com:465`, `SMTP_SECURE=true`) is implicit TLS.
Supporting both, selected by config, satisfies the spec and the environment.

## 3. Module layout

```
app/approvals/      service.py (extend)  executor.py (new)
app/notifications/  service.py  broker.py  email.py  router.py   (all new)
app/admin/          router.py  (new; approvals endpoints only — Phase 8 fills the rest)
```

Each unit has one job and a narrow interface:

- **`approvals.service`** owns the `approval_requests` row and its state machine. It does
  not know how any action is performed.
- **`approvals.executor`** owns dispatch, re-validation, and re-authorization. It does not
  know how a request was filed or decided.
- **`notifications.broker`** owns in-process fan-out. It has no database dependency.
- **`notifications.email`** owns SMTP and `outbound_emails`. It is the only write path to
  that table.

## 4. Approval lifecycle

`decide(db, principal, request_id, *, approve: bool, note: str) -> ApprovalRequest`

1. Load the request. If its status is not `pending`, raise a conflict (HTTP 409). This is
   the idempotency guard: a re-submitted approval must never execute the action twice.
2. Set `status` to `approved` or `denied`, plus `decided_by_user_id`, `decided_at`,
   `decision_note`. Write an `audit_log` row through `audit.record_audit` in the same
   transaction, so a rolled-back decision leaves no audit entry.
3. If denied: notify the requester (`approval_decided`) and return.
4. If approved: call `executor.execute()` synchronously, then set `status` to `executed` or
   `failed`, `executed_at`, and `execution_result`. Notify the requester with the outcome.

"Notify the requester" in steps 3 and 4 means an in-app `notifications` row when
`requester_user_id` is set. A guest requester has no such row available (§8.5); the
decision reaches them only if the executed action itself was an email to them. `decide()`
must skip the notification for a guest rather than fail on the `NOT NULL` column.

Execution is synchronous inside `POST /api/admin/approvals/{id}/decide`. The admin receives
the true terminal status in one round trip, the `executor` span nests under a single run,
and tests need no polling. The cost is up to ~10s latency on a `send_email` approval,
bounded by the SMTP timeout.

## 5. Executor

### 5.1 Re-validation and re-authorization (§9.2)

Before any handler runs, in this order, each failure producing `failed` with a recorded
reason and no side effect:

1. **Requester still exists and is active.** Reload the `User` row by `requester_user_id`.
   Missing or `is_active=False` → fail. For a guest requester (`requester_user_id IS
   NULL`), rebuild a guest principal from the conversation.
2. **Principal rebuilt from current state**, not from anything captured at request time.
   Role and clearance are read fresh.
3. **Payload re-validated** against that action's Pydantic schema. A payload that no longer
   validates → fail.
4. **`rbac.authorize(principal, "create_approval_request", original_args)`** re-run. A
   `Deny` → fail with its reason.

An approval is permission to perform the action as described, not a standing bypass of
policy.

**Honest scope of step 4:** `rbac.authorize` is currently role-based only — it ignores its
`arguments` parameter, and `create_approval_request` is not in `_GUEST_DENIED_TOOLS`. So
step 4 catches a requester whose *role* changed between filing and approval, and nothing
finer. Steps 1–3 carry the real weight. This step is included because §9.2 requires it and
because it becomes meaningful the moment argument-level rules land, not because it is
doing much work today. It must not be described in code comments or the phase report as
though it re-checks the payload against policy.

### 5.2 Handlers

Performed for real, because a real target exists:

| Action | Effect |
|---|---|
| `send_email` | §6 |
| `update_user_clearance` | Writes `users.clearance`; audited |
| `cross_department_ticket_assignment` | Calls `tickets.service.reassign`; emits `ticket_assigned` |
| `disclose_restricted_information` | Posts a `system` message into the conversation, attributed to the approving admin (§9.2) |

Simulated, because this system has no external IT infrastructure: `grant_system_access`,
`reset_credential`, `external_api_write`. Each records `{"simulated": true,
"absent_system": "<name>", ...}` in `execution_result`, so no consumer can mistake one for
a real grant.

A test parametrized over every `ApprovalActionType` member asserts a handler is registered.
Adding an action type without a handler fails the suite.

## 6. Email

`app/notifications/email.py` is the only write path to `outbound_emails`.

- **Transport:** `SMTP_SECURE=true` or port 465 → `smtplib.SMTP_SSL`; otherwise
  `smtplib.SMTP` plus `starttls()`. 10-second timeout. No retry on
  `SMTPAuthenticationError` — a bad credential is not a transient fault.
- **Allowlist:** `EMAIL_RECIPIENT_ALLOWLIST`, comma-separated, glob-matched. Set in `.env`
  to the operator's own address plus `*@northstar.example`; `.env.example` ships the
  `*@northstar.example` entry only. The seeded dataset's domain does not resolve, so demo
  sends fail loudly and visibly (§20), while the one real address genuinely delivers, which
  is what proves the gate. An empty allowlist rejects every recipient — fail closed, so a
  missing config value can never widen the blast radius.
- **Ordering:** the `outbound_emails` row is written before the socket opens (§9.3), so a
  crash mid-send is still visible. A non-allowlisted recipient is also recorded — as
  `failed`, `smtp_response="recipient not allowlisted"`. A rejection that leaves no trace
  would be less auditable than one that does.
- **Test seam:** a module-level transport singleton, overridden wholesale in tests,
  matching the `_anthropic_client` pattern in `app/chat/router.py`.

New config fields: `smtp_secure: bool = False`, `email_recipient_allowlist: str = ""`.

## 7. The invariant, enforced at the database

Migration, in order:

1. `UNIQUE (id, status)` on `approval_requests` — exists solely as a composite FK target.
2. `outbound_emails.approval_status_at_send`, type `approval_status`, `NOT NULL`. The table
   is empty, so no backfill is required.
3. Composite FK `(approval_request_id, approval_status_at_send)` → `approval_requests (id,
   status)` `ON UPDATE CASCADE`.
4. `CHECK (approval_status_at_send IN ('approved','executed','failed'))`.
5. `approval_execution` added to the `run_trigger` enum (§2.2).
6. Index on `notifications (user_id, read_at)`.

The FK forces the shadow column to track the approval's real status; the cascade keeps it
correct as `approved → executed`; the CHECK forbids the pre-approval states. Two violations
become impossible rather than merely untested: inserting an email row that names a
`pending` approval, and flipping an approval that already has email rows to `denied`.

## 8. Notifications

### 8.1 Broker

In-process, no database dependency: `publish(user_id, event)` and `subscribe(user_id)`,
fanning out to every open connection for that user. A per-subscriber bounded queue; a
subscriber that cannot keep up is dropped rather than allowed to block a publisher.

### 8.2 Publishing on commit

`notifications.service.notify()` stages the row and flushes. The actual `publish` fires
from a SQLAlchemy `after_commit` listener. Two failure modes this avoids: six call sites
each having to remember to publish, and an event delivered for a row whose transaction then
rolls back.

### 8.3 Stream

`GET /api/notifications/stream` **subscribes first, then replays** unread rows, deduping by
id. Replay-then-subscribe drops anything arriving in the gap between the two. A keepalive
comment every 15 seconds keeps proxies from closing an idle stream.

### 8.4 Triggers

All six from §10. `approval_decided` is emitted by §4. `attachment_requested` is emitted
from the existing `request_attachment_handler`. The four ticket triggers are retrofitted
into the existing call sites in `app/tickets/service.py`: `create_ticket`, `reassign`,
`transition_status`, and `resolve_ticket`.

### 8.5 Guests

Guests receive no in-app notifications. `notifications.user_id` is `NOT NULL` and guests are
deliberately not rows in `users` (§5.1). Their channel is email, subject to §6's allowlist.
This is a stated limit, not an oversight.

## 9. API surface

Both sets are the §14 endpoints, no more:

**Notifications** — `GET /api/notifications` · `GET /api/notifications/stream` (SSE) ·
`POST /api/notifications/{id}/read`

**Admin** (`require_role("admin")`, audit-logged on the mutating call) —
`GET /api/admin/approvals` · `POST /api/admin/approvals/{id}/decide`

The remaining §14 admin endpoints belong to Phase 8.

## 10. Testing

**Unit** — allowlist glob matching; transport selection (465 → SSL, 587 → STARTTLS);
per-action payload schema validation; handler-registered-for-every-action-type,
parametrized over the enum; broker fan-out and slow-subscriber drop.

**Integration** — approve → execute → notification row + SSE event + `outbound_emails` row;
deny → no email, notification records the denial; decide on a non-`pending` request → 409
and no second execution; requester deactivated between filing and approval → `failed` with
reason and no email; stream replay of unread rows on connect.

**Security (§19)** — a raw `INSERT` into `outbound_emails` naming a `pending` approval
raises `IntegrityError`; updating an approval that has email rows to `denied` raises
`IntegrityError`; a non-admin calling `decide` is rejected; an approval payload mutated in
the database between filing and approval fails re-validation rather than executing.

**Gate** — `test_phase6_gate.py`, mirroring `test_phase5_gate.py`: approve → execute →
assert the SSE event arrives on a live stream → assert the `outbound_emails` row reached
`sent`.

**Live** — one opt-in, marked test sending a genuine email to the allowlisted address,
excluded from the default run like every other live test.

## 11. Known hazards carried into this phase

- **The `start_run()` / `db_session` deadlock.** No Phase 6 table gains a foreign key to
  `runs`, which keeps the hazard out of production code. Tests that construct a `Run` must
  still follow the `_make_run` pattern in `tests/test_tickets_service.py`.
- **SSE tests need a real event loop.** They use `httpx.ASGITransport` streaming, not
  `TestClient`, whose synchronous iteration cannot interleave a publish with a read.
- **`User` row construction in fixtures** follows the existing Postgres fixture rules; see
  the project's recorded test-fixture gotchas before writing a new one.
