# Final Report — Phases 0-10

**Date:** 2026-09-04

## Build summary

| Phase | What it built | Spec | Plan |
|---|---|---|---|
| 0 | Scaffold, compose, config, Chroma, `ticketing` db, Alembic baseline | `docs/superpowers/specs/2026-08-24-agentic-helpdesk-design.md` | — |
| 1 | Models, auth, RBAC, seed (126 accounts) | same | — |
| 2 | RAG: chunking, both backends, ingestion | same | — |
| 3 | Tracing: spans, pricing, redaction | same | — |
| 4 | Agent: loop, tools, guardrails, streaming | same | — |
| 5 | Tickets, routing, tasks | same | — |
| 6 | Approvals, executor, notifications, email | `2026-08-27-approvals-executor-notifications-design.md` | `2026-08-27-approvals-executor-notifications.md` |
| 7 | Multimodal (image/PDF/audio attachments) | `2026-08-28-multimodal-design.md` | `2026-08-28-multimodal.md` |
| 8a | Admin API + incident dossier | `2026-08-29-admin-api-design.md` | `2026-08-29-admin-api.md` |
| 8b | Admin panel frontend (React/Vite) | `2026-09-02-admin-panel-frontend-design.md` | `2026-09-02-admin-panel-frontend.md` |
| 9 | Learning loop (reflection → lesson → embed → retrieval) | `2026-09-04-learning-loop-design.md` | `2026-09-04-learning-loop.md` |
| 10 | Hardening, README, full test pass (this report) | `2026-09-04-hardening-and-closeout-design.md` | `2026-09-04-hardening-and-closeout.md` |

Phases 0-9 are merged into `master`. Phase 10's six commits are on branch `phase-10-hardening` pending its own merge (see below).

## Measured eval numbers

Fresh run, 2026-09-04, after `tasks.py ingest` (1000 employee chunks, 300 helpdesk chunks) and `tasks.py eval` against the real Chroma instance and the shipped 60-query set:

| Metric | Value |
|---|---|
| Queries evaluated | 60 |
| Recall@5 | **0.7125** |
| Recall@10 | **0.8042** |
| MRR | **0.8108** |
| nDCG@10 | **0.7553** |
| Gate (Recall@5 ≥ 0.70) | **PASSED** |

Worst-performing queries are the same known, previously-diagnosed class: "which employees use `<tool>`" enumeration queries whose ground truth is an arbitrarily small sample of a much larger valid-answer set (e.g. Q047 "Which employees use Slack?" — documented in `tests/test_eval_retrieval.py`'s own comments as a dataset artifact, not a retrieval defect, independently reproduced twice in an earlier phase).

## Full test suite results

**Backend** (`tasks.py test`, real Postgres + Chroma, live-marked tests excluded): **801 passed, 9 deselected, 0 failed** in 491s.

**Frontend:**
- `npm test` (vitest): **251 passed (251)**, 28 test files, 36.71s.
- `npm run typecheck` (`tsc -b --noEmit`): clean.
- `npm run build` (`tsc -b && vite build`): clean, 106 modules transformed.

## What got hardened (Phase 10)

**1. `runs` row leak in `test_ingest_dataset.py`/`test_eval_retrieval.py`** (commit `43f3761`). Both test files called scripts that correctly bracket `start_run()`/`end_run()` but left the finalized `Run` row un-swept — 4-6 leaked rows per full-suite run. Fixed with a before/after `started_at`-range sweep fixture in each file. Proven: `select count(*) from runs where trigger='ingest_eval'` read 743 both before and after running `tests/test_ingest_dataset.py` and, separately, `tests/test_eval_retrieval.py`.

**2. Missing index on `lessons.created_at`** (commit `046bdbc`). `GET /api/admin/lessons` orders by `created_at DESC, id DESC` with no supporting index, unlike the identically-shaped `conversations` list. Added `index=True` to the column plus migration `c3f6a1d8e2b7`, mirroring `f9824ef578ed`'s exact pattern. Proven: `alembic upgrade head` applied cleanly and `alembic check` no longer lists `ix_lessons_created_at` as drift.

**3. `Subscription._offer` could raise `RuntimeError` out of `broker.publish`** (commit `ae0b30a`). If a subscription's captured event loop had closed, `call_soon_threadsafe` raised synchronously and uncaught, out of a function the module's own docstring says runs inside SQLAlchemy's synchronous `after_commit` hook — a real commit-path break, never before reproduced. Reproduced directly (a real closed `asyncio` loop assigned to `sub._loop`, then `broker.publish(...)`), confirmed the exact `RuntimeError: Event loop is closed` traceback, then fixed by catching it and marking the subscriber `dropped` — the same pattern already used for a full queue. All 8 broker tests pass.

**4. Four items of alembic drift from migration `211125c17904`** (commit `e03f72d`). `ApprovalRequest`, `OutboundEmail`, and `Notification` were all missing `__table_args__` declarations for a unique constraint, a composite FK with a CHECK, and an index that the live database already enforced. Declared each to match the migration exactly — no new migration, since the database was already correct. Proven: `alembic check` went from reporting 4 "New upgrade operations detected" items to "No new upgrade operations detected."

**5. Refresh-token rotation race** (commit `50cde1b`). `POST /api/auth/refresh` read `revoked_at`, checked it in Python, then wrote it — the identical TOCTOU shape phase 6's approval-decision race had. Reproduced with two real threads and two real sessions: one holds the row lock open (via `with_for_update()`) while the real `refresh()` endpoint function is called concurrently against the same token. Before the fix, the second call succeeded, minting a second valid token pair from a single-use token; after adding `.populate_existing().with_for_update()` (the exact pattern already proven for the approval race), it blocks then correctly 401s. All 11 auth-router tests pass. (One incidental finding while building this test: the failure mode itself — a genuinely-succeeding race — mints an extra `refresh_tokens` row the test's own cleanup had to account for, which is a small, specific confirmation that the race is real, not merely theoretical.)

**6. Sweep for tests defanged by `BaseHTTPMiddleware`** (audit, no code changes — see design spec §3.6). Enumerated every real `StreamingResponse` endpoint in the app (exactly three: the notification stream, the admin runs stream, and the chat turn stream) and every test whose docstring claims something about exception propagation or stream-close semantics. The two already-known-and-fixed instances (notification stream, admin runs stream) were confirmed still correctly assert on the generator directly. Empirically verified, with a standalone probe app carrying the identical `@app.middleware("http")` decorator, that a plain (non-streaming) route's unhandled exception still propagates correctly through `TestClient`'s `raise_server_exceptions=True` — the middleware's masking behavior is specific to `StreamingResponse`'s body-close signal, not to ordinary handlers. The third streaming endpoint (the chat turn) has no test making a false claim: its underlying `run_turn()` is independently tested at the function level to never raise, sidestepping the issue by construction. **Result: swept, confirmed clean, no additional instances found.**

**README**: added the missing "Phase 9: the learning loop" section (commit `277fc0f`), matching the existing phase sections' voice and structure.

## Manual end-to-end walkthrough

Performed live via the browser tool against the running dev stack (backend on `:8082`, frontend on `:5173`, real Postgres + Chroma), 2026-09-04, ~19:05-19:12 local time.

1. **Signed in as a seeded employee** (`mila.weber20`) and opened `/chat`. Described a specific VPN certificate problem: *"My VPN client has started rejecting the certificate the helpdesk issued after last week's root CA rotation... 'certificate verify failed: unable to get local issuer certificate'... blocking me from reaching any internal systems remotely."*

2. **First turn hit the 60-second wall-clock budget cap** (`TurnBudget.max_wall_seconds`) and was legitimately aborted — real, working budget enforcement (spec 12.3), not a bug. Run `1fd4f4e0-1dc3-4804-b82b-23ddc54f8073`: `aborted`, 1m 8s, 3 LLM calls, 3 tool calls, $0.110070. A follow-up message ("Please go ahead and create a ticket...") got a fresh per-turn budget and completed successfully: run `bf90b4c2-6189-4bed-8852-43d4ab9021dc`, `ok`, 55.8s, 5 LLM calls, 4 tool calls, $0.181210.

3. **A ticket was created: TCK-012376**, priority `high`, assigned to **Jonas Lewis (HD-005)**. Notably, the agent's own response explained that the automated specialist matcher's top suggestions (Kubernetes, Salesforce, privileged-access specialists) didn't actually fit a VPN problem, and it overrode the matcher using the knowledge base's explicit designation of HD-005 as the VPN/network specialist — a real instance of the agent reasoning about a bad tool result rather than trusting it blindly. It also correctly declined to take any credential-affecting action (no cert reissue, no trust-store change), deferring those to the specialist.

4. **Signed in as Jonas Lewis (helpdesk)**, found TCK-012376 on `/tickets`, transitioned it `open → assigned` (required by `LEGAL_TRANSITIONS` before a resolve is legal — matches `app/tickets/service.py`'s state machine exactly), then resolved it with a real, specific, non-routine resolution: a missing intermediate certificate in the MDM-distributed chain for pre-rotation enrollment batches, confirmed against two other affected users, fixed by pushing the missing bundle and flagging the gap to the PKI team.

5. **Reflection ran automatically in the background**, traced as two `Run`s: `7d3cb52f-cb12-4d4a-87a6-12fcd07c7e43` (`reflection`, `ok`, 16.3s, **a real `claude-opus-5` call** — `messages.parse`, 3,569 input tokens, 1,029 output tokens, $0.043570) followed by `6b00b5d9-7c1c-4540-9096-81a8af45705a` (`reflection`, `ok`, 2.0s, the embed). The model judged the resolution worth recording.

6. **Signed in as admin** and confirmed everything end to end:
   - `/admin/tickets`: TCK-012376 listed under **Resolved**, with the full routing rationale visible.
   - `/admin/lessons`: one lesson, *"Post-CA-rotation VPN 'unable to get local issuer certificate' = missing intermediate in MDM bundle for pre-rotation enrollment batches"*, category `vpn_network`, confidence `high`, status `active`, linked to the source ticket.
   - `/admin/audit`: both a `ticket.update` row (`open → assigned`) and a `ticket.resolve` row carrying the full resolution text, both attributed to Jonas Lewis's user id.
   - `/admin/traces`: every run from the walkthrough visible with correct duration, cost, and call counts, including the aborted first turn — nothing hidden or silently dropped.

No honest surprises beyond the budget-cap abort noted in step 2, which is itself evidence the budget enforcement works as designed rather than a defect.

## Closing verification

Backend suite, frontend suite, and eval were all re-confirmed green immediately before this report was written (see sections above) — the numbers cited here are the same run that gates this phase's merge, not an earlier development-time measurement.
