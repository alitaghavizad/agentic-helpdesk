# Phase 10 — Hardening, README, Full Test Pass — Design Specification

**Date:** 2026-09-04
**Status:** Approved for planning
**Working directory:** `D:\projects\ticketing_full`
**Parent spec:** `docs/superpowers/specs/2026-08-24-agentic-helpdesk-design.md` (§18 build phases, row 10: "Hardening, README, full test pass" — gate: "pytest green; measured eval numbers and a manual end-to-end walkthrough recorded in the final report")

---

## 1. Purpose

Phases 0-9 built the system; this phase closes it out. It is qualitatively different from every phase before it: there is no new subsystem, no new endpoint, no new screen. The work is six previously-diagnosed defects (found across phases 6-9's own reviews and one project-memory note, none newly discovered here), one missing README section, and a final report that proves the whole system works end to end rather than phase by phase.

### 1.1 Success criteria (parent spec §18, phase 10 gate)

"`pytest` green; measured eval numbers and a manual end-to-end walkthrough recorded in the final report" — the backend and frontend suites both pass, the retrieval eval is re-run and its numbers cited (not recalled from an earlier phase), and a real person-shaped walkthrough of the live system (chat → ticket → approval → resolution → lesson → admin review) is performed and written up, not merely asserted.

### 1.2 Non-goals

- **No new features, screens, or endpoints.** Every item below is a fix to something that already exists and is already diagnosed.
- **No broad security or tech-debt audit.** Scope is the six backlog items already on record, not a fresh sweep for unknown issues (see decision D1).
- **No performance tuning beyond the one missing index already identified.** Nothing here claims to make the system fast; it claims to make it correct and closed-loop.
- **No change to any phase 0-9 feature's behavior**, except where a fix in this phase is specifically that feature's bug (the refresh-token race, the notification broker's closed-loop case).

---

## 2. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Scope is exactly the six backlog items already on record, not a fresh audit** | Every item below was found and diagnosed by a prior phase's own review process (phases 6-9) or is explicitly recorded in the shipped README as a known follow-up. Re-scanning the whole codebase for new issues this late risks scope creep on the project's last phase; six known, bounded, independently-fixable items is a closeable list. |
| D2 | **Each backlog fix is direct execution, not subagent-driven-development** | Every prior phase used implementer-then-reviewer per task because the work was new design translated into new code, where an independent second reader catches drift from intent. These six items are the opposite shape: each is already root-caused, with the fix pattern already established elsewhere in this exact codebase (the mutation-tested collision guard from phase 9, the `.populate_existing().with_for_update()` fix from phase 6's approval race, the `dropped`-flag pattern already in `broker.py`, the `index=True` + migration pattern from `f9824ef578ed`). The remaining risk is verification, not design — so each item still gets a mutation-tested regression test and a real command run to prove the fix, just without a second implementer's pass. |
| D3 | **The alembic drift fix adds `__table_args__` declarations to match the live DB, never a new migration** | `alembic check` reports the live database already has all four objects (added by the hand-written migration `211125c17904`); the drift is that `app/db/models.py` doesn't declare them, not that the database is missing anything. Generating a new migration to "fix" this would be redundant DDL against an already-correct database and would not close the actual gap, which is metadata-only. |
| D4 | **The `runs`-leak fix lives in the test files, not in `scripts/ingest_dataset.py` / `scripts/eval_retrieval.py`** | Both scripts already bracket `start_run()`/`end_run()` correctly (no stuck-`RUNNING` bug) — the "leak" is that nothing outside the script ever deletes the row once the script's own bookkeeping finalizes it, exactly the same test-hygiene gap `conftest.py`'s existing `cleanup_run` fixture exists for everywhere else. Changing the scripts' signatures to return a run id, just so a test can pass it to `cleanup_run`, would touch production-shaped code for a test-only need; sweeping by `trigger + started_at` range (the pattern already used four times this session for the identical class of leak) needs no script changes at all. |
| D5 | **The refresh-token race fix uses `.populate_existing().with_for_update()`, not a unique constraint or an application-level mutex** | This is the identical shape phase 6's approval-decision TOCTOU bug was fixed with (`decide()`, per project memory) — a status read, a Python-level check, then a status write, racing another request doing the same. `with_for_update()` alone is a documented trap in this codebase (it takes the lock but the ORM identity map still returns the stale pre-lock instance) — `populate_existing()` is required alongside it, and this project has already paid to learn that once. Re-using the identical, already-proven pattern is lower-risk than introducing a new concurrency primitive for a problem this codebase has already solved once. |
| D6 | **The final report is a new file under `docs/superpowers/reports/`, not a README section** | The README is a setup/reference document — every existing phase section in it describes what to configure and how to run something, not a narrative of what was tested or found. A closing project report (measured numbers, a walkthrough narrative, what got hardened and why) is a different kind of document for a different reader and does not belong interleaved with the "how do I start this locally" content the README exists for. |
| D7 | **The manual end-to-end walkthrough is performed live, via the browser tool, against the real running stack** | Consistent with every prior phase's gate in this project (the phase 8b Playwright gate, the live dossier call, the live reflection call) — a walkthrough "recorded" from memory or from what the code is supposed to do is not a walkthrough, it's a claim. The report must be able to say what was actually clicked and what actually happened. |

---

## 3. The six backlog items

### 3.1 `runs` row leak in `test_ingest_dataset.py` / `test_eval_retrieval.py`

Both files call scripts (`scripts/ingest_dataset.py::main()`, `scripts/eval_retrieval.py::run_eval()`) that each commit one `Run(trigger=RunTrigger.INGEST_EVAL)` row via `start_run()`/`end_run()`, with no test-side cleanup — measured at 4 rows from `test_ingest_dataset.py` (one per `main()` call: one in `test_ingest_dataset_closes_backend_on_chunking_failure`, three across the two-calls-plus-a-finally-call shape of `test_ingest_dataset_populates_both_collections_and_is_idempotent`) plus 1-2 from `test_eval_retrieval.py`'s recall-retry loop, totaling the previously-observed 5-10 rows per full-suite run.

**Fix:** add a fixture to each test file (or a shared one in `conftest.py` if the shape is identical, decided during implementation) that captures the max `started_at` for `Run.trigger == RunTrigger.INGEST_EVAL` before the test body runs, then in a `finally`/fixture-teardown sweeps every such row created after that point — the same before/after `started_at`-range pattern already used four times this session in phase 9's own leak fixes, including for a case (`test_admin_lessons_reembed.py`'s idempotent-delete test) where a single test creates more than one row.

**Test:** the fix's own correctness is provable by a direct row-count check (query count before, run the test, query count after, assert unchanged) — the same verification method already used for every leak fix in phase 9, not a new pattern.

### 3.2 `lessons.created_at` has no index

`GET /api/admin/lessons` (`app/admin/router.py` → `app/admin/queries.py::list_lessons`) does `ORDER BY lessons.created_at DESC, lessons.id DESC`, but `Lesson.created_at` (`app/db/models.py`) has no index — unlike the identically-shaped `Conversation.created_at`, which already carries `index=True` for the equivalent conversations-list endpoint (added in migration `f9824ef578ed`, which also indexed `audit_log`'s four filter/sort columns and `runs.started_at`).

**Fix:** add `index=True` to `Lesson.created_at`'s column definition, mirroring `Conversation.created_at`'s exact style, plus a new Alembic migration containing exactly `op.create_index(op.f('ix_lessons_created_at'), 'lessons', ['created_at'], unique=False)` (and the matching `drop_index` in `downgrade()`), following `f9824ef578ed`'s own pattern verbatim.

**Test:** `alembic check` reports clean after `alembic upgrade head`; no application-level test is meaningful for an index (query plans aren't asserted anywhere else in this codebase either).

### 3.3 `Subscription._offer` can raise `RuntimeError` out of `broker.publish`

If a `Subscription`'s captured event loop (`app/notifications/broker.py`) has since closed, `self._loop.call_soon_threadsafe(self._put, event)` raises `RuntimeError: Event loop is closed`, uncaught, propagating out of `publish()` — which is called from SQLAlchemy's synchronous `after_commit` hook per the module's own docstring, meaning this would break a real commit path if it ever fired. Never reproduced; the file already has an established pattern for "this subscriber is unusable" (`_put`'s `QueueFull` branch sets `self.dropped = True`, and `get()` turns that into `SubscriberDropped`, which the SSE endpoint turns into a closed, reconnectable stream with replay).

**Fix:** wrap the `call_soon_threadsafe` call in `try`/`except RuntimeError`, setting `self.dropped = True` on catch and returning — mirroring the `QueueFull` branch exactly, keeping `publish()` itself exception-free for every other subscriber regardless of one bad one.

**Test:** construct a `Subscription` with a real, already-closed event loop object (or a stand-in whose `call_soon_threadsafe` raises `RuntimeError`, chosen during implementation based on which is easier to construct correctly) and confirm (a) `publish()` does not raise and (b) the subscription's `dropped` flag is set, then that a subsequent `get()` raises `SubscriberDropped` — proven by mutation (remove the `try`/`except`, watch the same test fail with the real `RuntimeError`) before restoring.

### 3.4 Four items of alembic drift (migration `211125c17904` vs. `app/db/models.py`)

`alembic check` reports the live database has, and the ORM models do not declare:

- `uq_approval_requests_id_status` — a `UniqueConstraint` on `approval_requests(id, status)`, added solely so it can be the target of a composite FK.
- `ix_notifications_user_id_read_at` — an `Index` on `notifications(user_id, read_at)`.
- `fk_outbound_emails_approval_status` — a composite `ForeignKeyConstraint` from `outbound_emails(approval_request_id, approval_status_at_send)` to `approval_requests(id, status)`, `onupdate="CASCADE"`.
- `ck_outbound_emails_approved_before_send` — a `CheckConstraint` on `outbound_emails` restricting `approval_status_at_send` to `('approved', 'executed', 'failed')`.

**Fix:** add `__table_args__` to `ApprovalRequest` (the `UniqueConstraint`), `Notification` (the `Index`), and `OutboundEmail` (the `ForeignKeyConstraint` and the `CheckConstraint`) in `app/db/models.py`, each matching the migration's names and definitions exactly. No new migration (see D3).

**Test:** `alembic check` exits clean (no detected upgrade operations) — the direct, authoritative check for this class of drift, already used to diagnose it.

### 3.5 Refresh-token rotation race

`POST /api/auth/refresh` (`app/auth/router.py::refresh()`) reads the stored `RefreshToken` row, checks `revoked_at is None` in Python, then sets `revoked_at` and commits — the identical read-check-write shape phase 6's approval-decision idempotency bug had, meaning two concurrent refresh requests presenting the same token can both pass the check and both successfully rotate it, issuing two valid token pairs from what should be a single-use token.

**Fix:** `.populate_existing().with_for_update()` on the `RefreshToken` query, so a second concurrent request blocks until the first's transaction commits, then re-reads the now-`revoked_at`-set row (via `populate_existing()`, without which the ORM's identity map would return the stale pre-lock instance — the exact trap phase 6's fix already documented) and correctly 401s.

**Test:** two real, concurrently-issued `POST /refresh` requests presenting the same refresh token against a genuinely committing session (mirroring `test_admin_mutations.py`'s `_committing_client` pattern, since `db_session`'s savepoint-scoped fixture cannot show real cross-connection blocking) — assert exactly one succeeds and the other 401s, proven by mutation (revert to a plain, unlocked query; confirm both requests can succeed) before restoring the fix.

### 3.6 Sweep for other tests defanged by `BaseHTTPMiddleware`

Phase 8a's final review found that adding the upload-size-cap middleware (`@app.middleware("http")`, phase 7) put Starlette's `BaseHTTPMiddleware` in front of every route, which closes the client-facing body identically whether a downstream handler returned or raised — silently defanging a Phase 6 SSE-drop regression test that asserted this distinction over HTTP. That review flagged "worth a Phase 10 sweep for other tests whose stated guarantee predates that middleware" without performing the sweep.

**Fix:** grep every test whose docstring or comment asserts something about HTTP-level exception propagation, streaming-response close behavior, or a distinction between a "handled" and "unhandled" failure reaching the client — for each, confirm (by reverting the code path it claims to protect and watching the test actually fail) that it still tests what it claims. This is an audit, not a known fix; its outcome is either "confirmed clean, no further action" or a small number of additional targeted fixes discovered during the sweep, each scoped and fixed the same way `test_notification_stream_drop`'s equivalent was (assert on the generator directly, not over HTTP, where the middleware's body-close behavior would otherwise mask the real assertion).

**Test:** the sweep's own methodology (revert-and-confirm-failure per candidate test) is the verification; no new test is written unless the sweep finds an actual gap.

---

## 4. README and final report

### 4.1 README: add the missing Phase 9 section

Every phase 6/7/8a/frontend section in the README follows the same shape: what's new, key design decisions worth knowing, and how to run its live/opt-in test. Phase 9 has no section. Add one covering: the two new endpoints' behavior is unchanged (reflection is invisible background work, not a new endpoint) — so the section documents the background reflection flow itself (triggered from ticket resolution, traced as its own `Run`, writes `knowledge/lessons/*.md`, embeds into Chroma, retrievable via `search_lessons`), the `should_record` judgment call and its poison-the-corpus mitigation, and the live gate command (`pytest tests/test_learning_live.py -v -m live_reflection`), matching the existing Phase 8a section's "Live dossier check" subsection format.

### 4.2 Final report

New file: `docs/superpowers/reports/2026-09-04-final-report.md` (date adjusted to whatever day this task actually executes). Contents:

- **Build summary** — phases 0-10, one line each, pointing at each phase's spec/plan.
- **Measured eval numbers** — a fresh `make eval` run (Recall@5/10, MRR, nDCG@10), cited with the date it was measured, not carried over from an earlier phase's number.
- **Full test suite results** — backend (`tasks.py test`) and frontend (`npm test`, `npm run typecheck`, `npm run build`) all green, with the exact counts.
- **What got hardened** — one paragraph per backlog item from §3, each citing its fix commit and the command that proves it.
- **Manual end-to-end walkthrough** — performed live via the browser tool against the running dev stack: sign in as a seeded employee, open a chat, describe a problem that routes to a ticket, sign in as the matching helpdesk specialist, resolve the ticket, confirm a lesson gets recorded (or honestly note if the model judges it too routine, per the same judgment call `test_learning_live.py` already accounts for), sign in as admin, and confirm the ticket, the run trace, and the lesson are all visible and correct in the admin panel. Written up as what was actually clicked and actually seen, with screenshots only if they clarify something text doesn't.

---

## 5. Testing strategy

- **Backlog items 3.1-3.5**: each gets a mutation-tested regression test (write the test, confirm it fails against the pre-fix code with the predicted failure, apply the fix, confirm it passes) except 3.2 (an index has no meaningful application-level test) and 3.4 (verified by `alembic check`, not a pytest test).
- **Backlog item 3.6**: an audit whose own methodology is the verification (see §3.6).
- **Closing gate**: full backend suite (`tasks.py test`) and full frontend suite (`npm test`, `npm run typecheck`, `npm run build`) both green, immediately before the final report is written, so the report's cited numbers are the same run that gates the merge — not run once during development and re-cited later.

---

## 6. Non-obvious risks

- **The refresh-token concurrency test needs two genuinely concurrent requests against a genuinely committing session** — `db_session`'s savepoint fixture cannot show real row-locking (established repeatedly this project: Phase 6's approval-decision test, and every "committing_client" pattern since). Get this wrong and the test will pass whether or not the fix works, exactly the false-confidence failure mode Phase 6's own TOCTOU bug review already warned about.
- **The alembic drift fix must not introduce NEW drift.** Adding `__table_args__` with a definition that doesn't byte-for-byte match what `211125c17904` actually created (e.g. a different `onupdate` on the composite FK, or a differently-ordered column list) would trade one `alembic check` failure for another. Verify against the migration's literal `upgrade()` body, not against a assumption of what it "should" say.
- **The `runs`-leak fix must not change what `test_ingest_dataset.py`/`test_eval_retrieval.py` actually verify** (real chunking/ingestion/recall behavior against real Chroma) — the fix is purely additive cleanup, and must not, for example, accidentally sweep a Run row a still-running assertion depends on reading.

---

## 7. Self-review

**Placeholder scan:** every backlog item names its exact file, the exact defect, and the exact fix pattern (with a cited precedent already in this codebase) — none are left as "investigate and fix," since the investigation already happened during this design's own research pass.

**Internal consistency:** §3.1's fix (sweep in test files) does not conflict with §6's risk about not disturbing what those tests verify — the sweep is additive teardown, occurring after each test's own assertions have already run. §3.4's "no new migration" (D3) is consistent with §6's drift-must-not-recur risk, since both are about matching existing DB state rather than changing it.

**Scope check:** six independently-fixable items plus two documentation deliverables is bounded enough for one implementation plan; none of the six depends on another (they touch disjoint files: `tests/test_ingest_dataset.py`+`tests/test_eval_retrieval.py`; `app/db/models.py`+a new migration; `app/notifications/broker.py`; `app/db/models.py` again (different classes, can share a task with 3.2's model edit or run separately — decided during planning); `app/auth/router.py`; a grep-driven audit with no fixed target file), so they can be planned and executed as independent tasks.

**Ambiguity check:** §3.6's sweep is deliberately open-ended in outcome (it may find nothing) but not in method — "revert the guarantee, confirm the candidate test fails, restore it" is the same concrete verification method used everywhere else in this spec, applied to an unknown set of candidates rather than a known one.
