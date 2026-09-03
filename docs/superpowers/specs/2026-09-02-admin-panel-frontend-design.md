# Phase 8b — Admin Panel Frontend — Design Specification

**Date:** 2026-09-02
**Status:** Approved for planning
**Working directory:** `D:\projects\ticketing_full`
**Parent spec:** `docs/superpowers/specs/2026-08-24-agentic-helpdesk-design.md` (§14 API surface, §15 admin panel, §16 repository layout)
**Predecessor:** Phase 8a, `docs/superpowers/specs/2026-08-29-admin-api-design.md` (merged as `edf52c9`)

---

## 1. Purpose

Phase 8a finished the admin read surface as a typed HTTP API. Nothing consumes it: `frontend/` does not exist, and the whole product is currently reachable only through `curl`. This phase builds the React SPA — the twelve screens of parent spec §15 plus login, chat and tickets — and closes the small number of backend gaps that make three of those screens impossible to build as specified.

The phase is complete when parent-spec success criterion 1 holds in a browser: `admin`/`admin` signs in and reaches the admin panel, a seeded dataset identity signs in and reaches chat, and every screen renders against seeded data.

### 1.1 Non-goals

Inherited from the parent spec §1.2 and restated because they shape this design more than anything else:

- **No responsive design.** "Usable on a laptop" is the whole requirement. No mobile layouts, no breakpoint matrix.
- **No production build pipeline, CDN, SSR, or deployment.** `npm run dev` against a local backend is the delivery target.
- **No design system, theming, or dark mode.** One light theme.
- **No i18n, no accessibility audit.** Semantic HTML and keyboard-reachable controls, not a WCAG conformance programme.
- **No component library.** See D5.
- **No offline support, service worker, or optimistic-update framework.**

---

## 2. Decisions

Settled here; changing one means revisiting this document.

| # | Decision | Rationale |
|---|---|---|
| D1 | **SSE is read with `fetch` + `ReadableStream`, never `EventSource`** | Every route, both streams included, authenticates from an `Authorization: Bearer` header (`app/deps.py`). `EventSource` cannot send headers. The alternative — accepting the token as a query parameter — writes JWTs into access logs, proxy logs and `Referer`, which is a real credential leak in exchange for convenience. `POST /api/conversations/{id}/messages` is SSE-over-POST and needs `fetch` regardless, so one reader serves all three streams. |
| D2 | **Types are generated from `/openapi.json`; the transport is hand-written** | `openapi-typescript` emits types only, no runtime. The runtime needs 401-refresh interleaving, SSE, and an abort story that no generator produces well. Generating types keeps the 45 published schemas authoritative; hand-writing ~150 lines of transport keeps the interesting behaviour readable and testable. A full client generator (orval, openapi-generator) would generate a runtime we would then have to fight. |
| D3 | **Four backend gaps are closed in this phase, first** | Parent spec §15 requires a Conversations detail showing "the transcript beside its span tree". No endpoint returns messages at all, so that screen — and a Chat page that survives a page reload — cannot be built. Three more endpoints publish `object` where a real schema exists. This is not scope creep: it is the API the specified screens require. See §4. |
| D4 | **Cross-origin to `http://localhost:8000`, not a Vite dev proxy** | The backend already configures CORS for `http://localhost:5173` with credentials (`app/main.py`, `settings.frontend_origin`). A dev proxy would make the browser same-origin and leave that configuration permanently unexercised, so the first non-proxied deployment would be the first test of it. Going direct exercises the real CORS and cookie path from day one. Cost: SSE and the refresh cookie must both work cross-origin, which §6.2 addresses explicitly. |
| D5 | **Plain Tailwind plus a dozen local primitives; no shadcn/ui, no Radix** | Twelve screens of tables, badges, a modal and a tab strip. The primitives needed are small enough to own outright, and owning them avoids a generator, a `components.json`, and a Radix dependency tree in a project whose stated ceiling is "usable on a laptop". **This is the closest call in this document** — Radix would give better focus management on the one modal. Revisit if the approvals modal or the trace waterfall turns out to need real focus-trap behaviour. |
| D6 | **TanStack Query owns all server state. No Redux, no Zustand** | Every screen is a read of a paginated endpoint with a refetch. The only genuinely client-side state is the access token and the live-event buffers, and both are one React context each. |
| D7 | **The phase gate is Playwright against the real backend and the seeded database** | Component tests behind MSW prove that a component renders a fixture; they cannot discharge "every screen renders against seeded data", which is the parent spec's phase 8 gate. Only a browser driving the real stack can. Vitest covers logic; Playwright covers the gate. |
| D8 | **The generated `schema.d.ts` and the `openapi.json` it came from are both committed, with a drift check** | A generated file that is not committed is a file nobody notices going stale. `npm run api:check` regenerates both and fails on any diff, so a backend schema change that breaks the client fails loudly rather than at runtime. |

---

## 3. Architecture

```
frontend/
├── package.json  vite.config.ts  tsconfig.json  playwright.config.ts
├── openapi.json                    # committed snapshot, dumped from the backend
└── src/
    ├── api/
    │   ├── schema.d.ts             # GENERATED by openapi-typescript — never edited
    │   ├── client.ts               # typed fetch: auth header, 401→refresh→retry, errors
    │   ├── sse.ts                  # fetch-based SSE reader (D1)
    │   └── endpoints/              # one small module per API area, typed off schema.d.ts
    ├── auth/
    │   ├── AuthContext.tsx         # access token in memory, principal, login/logout
    │   └── RequireRole.tsx         # route guard
    ├── components/                 # the local primitives of D5
    ├── hooks/                      # useNotifications, useRunStream, useChatTurn
    ├── lib/                        # formatting: usd, tokens, duration, dates
    └── pages/
        ├── Login.tsx  Chat.tsx  Tickets.tsx
        └── Admin/{Overview,Conversations,Traces,Approvals,Tickets,Users,Lessons,Audit,Costs}.tsx
```

This matches parent spec §16 exactly, with `openapi.json` and `playwright.config.ts` added.

### 3.1 Module boundaries

The rule that keeps this navigable: **`src/api/` is the only place that knows about HTTP**, and **`src/pages/` is the only place that knows about layout**. Everything crossing between them is a typed hook.

- `api/client.ts` — knows about tokens, status codes and retries. Knows nothing about React.
- `api/endpoints/*` — one function per endpoint, parameters and return type both derived from `schema.d.ts`. No React, no formatting.
- `hooks/*` — wraps endpoints in TanStack Query or a stream subscription. Knows nothing about markup.
- `pages/*` — composes hooks and components. Contains no `fetch` and no URL string.

A page that needs a URL string, or an endpoint module that imports React, means a boundary has been crossed and the fix is to move the code, not to add an import.

---

## 4. Backend changes

Additive backend changes, each required by a screen this phase needed. The original four (below) came from Task 0; five more widenings were added by review as later screens exposed gaps — see §4.6.

Additive only — no field is ever removed or renamed. Several existing response models are *widened* (gain new fields) rather than left untouched; see §4.6 for the full list.

### 4.1 `GET /api/conversations/{id}` returns its transcript

Today it returns `{id, title, status}`. The messages exist (`chat.service.load_history`) but no endpoint exposes them, so a Chat page shows only what streamed into the current tab and loses everything on reload.

Add `messages: list[MessageView]`, where `MessageView` is `{id, role, content, created_at, run_id}` and `content` is the stored content-block list. System messages stay excluded, matching `load_history`. Authorization is unchanged — `get_conversation` already scopes to owner, guest-by-email, or admin.

### 4.2 `GET /api/admin/conversations/{id}` (new)

Parent spec §15 requires the Conversations detail to show "the transcript beside its span tree". Returns `{conversation: ConversationSummary, messages: list[MessageView], runs: list[RunSummary]}` — the runs being every `runs` row whose `conversation_id` matches, newest first, so the screen can link each into the existing `GET /api/admin/runs/{id}/trace`.

Admin-gated and audited on read? **No.** The audit log records mutating calls (parent spec §14); adding a row per detail view would bury real events under navigation noise. Consistent with the other admin read endpoints, which are unaudited.

### 4.3 Four untyped responses gain their schemas

| Endpoint | Publishes today | Should publish |
|---|---|---|
| `POST /api/admin/tickets/{id}/dossier` | `object` | `IncidentDossier` — the model already exists in `app/admin/dossier.py` and is already validated at build time |
| `PATCH /api/admin/users/{id}` | `object` | `UserPatchResult {id, role, clearance}` |
| `PATCH /api/admin/lessons/{id}` | `object` | `LessonSummary` |
| `DELETE /api/admin/lessons/{id}` | `object` | `LessonDeleteResult {id, status, archived: bool}` |

Declarations only. Each router still builds its payload field by field, per the rule already stated in `app/admin/schemas.py`: a column must not join the API by being added to a table.

### 4.4 An OpenAPI dump script

`backend/scripts/dump_openapi.py` writes `app.openapi()` to `frontend/openapi.json`. It imports the app but starts no server and touches no database, so it runs in CI and on a machine with Docker stopped. This is what `npm run api:check` calls.

### 4.5 What is deliberately NOT added

- **No `conversation_id` filter on `GET /api/admin/runs`.** §4.2 already returns a conversation's runs; a second path to the same rows is a second thing to keep correct.
- **No pagination on `GET /api/admin/approvals`.** The pending queue is bounded by how fast a human decides; the list endpoint already filters by status.
- **No new SSE channels.** Approvals and tickets refresh by polling (§6.3).

### 4.6 Widenings added after Task 0

Each of these adds fields to an existing response; none removes or renames one. Every widening was forced by a specific screen this phase built, found during that screen's own review rather than anticipated in Task 0.

| Model | Gained | Because |
|---|---|---|
| `PrincipalResponse` | `username`, `full_name` | The admin's own name was unreadable in the NavBar (rendered as a raw UUID). |
| `NotificationResponse` | `created_at` | The bell's timestamp had no source field. |
| `ConversationSummary` | `username`, `full_name` | The admin Conversations list rendered a registered participant as a raw UUID even though the search already matched on username. |
| `CostByModel` | `unpriced_calls` | A wholly-unpriced model's spend was silently coalesced to `$0.00`, indistinguishable from genuinely-zero spend. |
| `CostTotals` | `unpriced_calls` (counted via its own query, independent of `CostByModel`'s scope) | Same problem at the totals level. |
| `TicketSummary` | `matched_specialization`, `assignment_rationale`, `assignment_score` | The admin ticket board needed the routing decision on every card and was firing one extra request per ticket to get it. |

---

## 5. Screens

Twelve routes. Every one is a read of an endpoint that exists after §4.

| Route | Role | Contents | Endpoints |
|---|---|---|---|
| `/login` | public | Sign-in form and a guest tab (name + email) | `POST /auth/login`, `POST /auth/guest` |
| `/chat` | any | Conversation list, transcript, composer, attachment upload, live turn rendering (§6.4) | `GET/POST /conversations`, `GET /conversations/{id}`, `POST /conversations/{id}/messages`, `POST /conversations/{id}/attachments` |
| `/tickets` | any | Own tickets, scoped server-side; staff see the edit controls and Resolve | `GET /tickets`, `GET/PATCH /tickets/{id}`, `POST /tickets/{id}/resolve` |
| `/admin` | admin | Runs today, spend today, pending approvals, open tickets, error rate, live activity feed | `GET /admin/overview` + the run stream |
| `/admin/conversations` | admin | Searchable list; detail is transcript beside the run's span tree | `GET /admin/conversations`, `GET /admin/conversations/{id}`, `GET /admin/runs/{id}/trace` |
| `/admin/traces` | admin | Run list with cost/latency/status; detail is the collapsible waterfall; live updates | `GET /admin/runs`, `GET /admin/runs/{id}/trace`, `GET /admin/runs/stream` |
| `/admin/approvals` | admin | Pending queue with justification, risk, payload, conversation link; approve/deny with a note; decided items keep their execution result | `GET /admin/approvals`, `POST /admin/approvals/{id}/decide` |
| `/admin/tickets` | admin | Board by status; assignee, specialization match, rationale, **Generate dossier** | `GET /tickets`, `POST /admin/tickets/{id}/dossier` |
| `/admin/users` | admin | 126 accounts; role and clearance editable; dev-seed badge | `GET /admin/users`, `PATCH /admin/users/{id}` |
| `/admin/lessons` | admin | Lessons with source ticket; edit and archive | `GET /admin/lessons`, `PATCH/DELETE /admin/lessons/{id}` |
| `/admin/audit` | admin | Filterable append-only log | `GET /admin/audit` |
| `/admin/costs` | admin | Spend by day, model, user and trigger; token totals; cache hit rate | `GET /admin/costs` |

After login the landing route is by role: admin → `/admin`, helpdesk → `/tickets`, everyone else → `/chat`. Every role may still reach `/chat` and `/tickets`; only `/admin/*` is guarded.

### 5.1 The trace waterfall

`RunTrace.roots` is already a nested `SpanNode` tree, so the component is a recursive row renderer, not a layout engine. Each row shows kind, name, a duration bar proportional to the run's total duration, model, the four token counts, and USD cost; expanding a row reveals its redacted `input`/`output`. Children indent.

Two rules the data forces:

- **`cost_usd: null` renders the word "unpriced", never `$0.00`.** Parent spec §17: a confidently wrong number is worse than none.
- **`truncated: true` renders a banner above the tree** saying spans were dropped at the cap. A silently shortened waterfall reads as a run that simply stopped.

### 5.2 The dossier

Rendered as a card, section per field, with a Download JSON button. It is schema-validated server-side, so a failure is an error state with the server's message — never a partially-rendered card. The call takes tens of seconds (36.5s measured in Phase 8a), so the button enters a pending state with an explicit "this runs a model call" note and does not time out client-side before the server does.

### 5.3 Costs

Four groupings and a totals row, all rendered as tables plus one bar chart of spend by day. No charting library: a bar chart of at most 30 days is a `<div>` per day with a percentage width. Cache hit rate renders as a percentage of a stated denominator, matching what `Costs.totals` actually means.

---

## 6. Cross-cutting behaviour

### 6.1 Authentication

`POST /auth/login` returns an access token in the body and sets an httpOnly refresh cookie scoped to `path=/api/auth`, `SameSite=Strict`.

- The **access token lives in memory only** — a React context, never `localStorage`. An XSS that can read `localStorage` can exfiltrate a bearer token that outlives the tab; one that cannot read the cookie cannot mint a new one.
- **A page reload therefore starts with no access token**, and the app's first action is `POST /auth/refresh` with `credentials: 'include'`. Success rehydrates the session; 401 routes to `/login`. This is the only supported session-restore path, and it is why the refresh cookie matters.
- **Guests get no refresh cookie** (`_issue_tokens` issues one only for `kind == "user"`). A guest reload therefore ends the guest session and returns to `/login`. That is the backend's existing behaviour, surfaced honestly rather than papered over.

### 6.2 The transport

`api/client.ts` is one function plus a refresh mutex:

1. Attach `Authorization: Bearer <token>` and `credentials: 'include'`.
2. On 401, call `POST /auth/refresh` **once**, shared across every request that raced into the same 401 — a single in-flight promise, not one refresh per failed call. Retry the original request with the new token.
3. If the refresh itself 401s, clear the token and route to `/login`.
4. Anything else non-2xx throws a typed `ApiError {status, detail}`, reading FastAPI's `{"detail": ...}` shape.

Cross-origin per D4, so every call sets `credentials: 'include'` and the backend's existing `allow_credentials=True` covers it.

### 6.3 Live data

Three streams, one reader (`api/sse.ts`): read the response body, split on `\n\n`, ignore `:` comment frames (the backend sends `: keepalive` every 15s), `JSON.parse` each `data:` line.

| Stream | Consumer | Reconnect |
|---|---|---|
| `GET /api/notifications/stream` | Bell in the app shell, for signed-in users only — **not opened for guests**, which the endpoint 403s | Exponential backoff to 30s |
| `GET /api/admin/runs/stream` | Overview activity feed and the Traces list | Backoff, then a full refetch of `GET /admin/runs` on reconnect, because the server drops a slow subscriber rather than queueing (`SubscriberDropped`) and history lives in the list endpoint |
| `POST /api/conversations/{id}/messages` | One chat turn; ends at `done` | Never — a turn is not resumable |

Everything else polls through TanStack Query: 30s on the approvals queue and the overview counters, on refocus elsewhere.

### 6.4 A chat turn

The turn stream emits nine event types (`app/agent/loop.py`): `token`, `tool_start`, `tool_end`, `task_recorded`, `ticket_created`, `approval_requested`, `attachment_request`, `error`, `done`. The transcript renders `token` as accumulating assistant text; `tool_start`/`tool_end` as a collapsed "used *tool*" row that marks itself failed on `is_error`; the four outcome events as inline cards linking to the created ticket or approval; `error` as an inline error bubble that ends the turn.

**`done` carries `run_id`, and an admin sees a "view trace" link from it.** That is the shortest path from a visible answer to the span tree that produced it.

### 6.5 Errors and empty states

Every list screen distinguishes three states — loading, empty, failed — and never renders an empty table for a failed request. A 403 renders "you do not have access to this", not a blank screen. This is stated once here rather than repeated per screen; it applies to all twelve.

---

## 7. Testing

Three layers, each proving something the others cannot.

**Vitest + Testing Library + MSW** — the logic that is genuinely client-side:
- the refresh mutex: concurrent 401s trigger exactly one `POST /auth/refresh` (assert the call count, not just the outcome);
- the SSE parser: split frames, comment frames, a frame split across two chunks, a truncated final frame;
- the chat reducer: the nine event types produce the expected transcript;
- formatting: `unpriced` for null cost, token and duration formatting;
- the role guard: a non-admin principal is redirected off `/admin/*`.

**Playwright against the real backend and seeded database** — the phase gate (D7):
1. `admin`/`admin` signs in and lands on `/admin`;
2. a seeded employee signs in and reaches `/chat`;
3. every one of the twelve routes renders its own content against seeded data — asserted on a screen-specific element, not on "no error thrown";
4. a guest signs in, reaches `/chat`, and never opens the notification stream (asserted on the network, since the endpoint would 403 — and no turn is sent, so this test needs no API key);
5. a non-admin navigating to `/admin` is redirected.

Test 3 is the parent spec's phase 8 gate, and it is the reason Playwright is here at all.

**Type checking** — `tsc --noEmit` and `npm run api:check` both run in the test task. A backend schema change that breaks a screen fails as a type error, which is the entire point of generating types from the live schema.

### 7.1 How the gate is honestly reported

The Playwright run needs Postgres, Chroma, a migrated and seeded database, and a running backend. When any of that is unavailable the gate is reported as **not run**, never as passed. Following `feedback_verify_by_the_real_metric`: the acceptance bar is "the screen rendered its own data", asserted per screen, not "the suite exited 0".

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| SSE-over-`fetch` behaves differently from `EventSource` on reconnect and abort | One reader, unit-tested against chunk-splitting; every stream ties its `AbortController` to component unmount so a navigated-away stream cannot leak a connection. The backend already drops slow subscribers rather than growing a queue. |
| Cross-origin credentials (D4) fail on the refresh cookie | The cookie is `SameSite=Strict` on `path=/api/auth`; `localhost:5173` and `localhost:8000` differ only by port, which SameSite does not consider, so it is sent. Verified by a Playwright reload-and-stay-signed-in test rather than by assertion here. |
| The generated types drift from the running backend | `npm run api:check` regenerates and diffs; it runs in the test task, not only in CI. |
| Twelve screens is a lot of surface for one review | The plan stages them so each screen is a separate task with its own review, matching how phases 6 through 8a were executed. |
| Tailwind v4, Vite 8, TypeScript 7 are all recent majors | Versions are pinned in `package.json` at scaffold time, and the scaffold task's gate is "`npm run build` and `tsc --noEmit` both clean" before any screen is written — so a toolchain problem surfaces in task 1, not in task 9. |

---

## 9. Open items

None blocking. `ANTHROPIC_API_KEY` is required only for the dossier button and a live chat turn to do anything; every other screen renders from seeded data without it.
