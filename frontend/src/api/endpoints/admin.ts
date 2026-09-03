import { apiFetch, apiStream } from "../client";
import type { components } from "../schema";

export type Overview = components["schemas"]["Overview"];
export type Costs = components["schemas"]["Costs"];
export type CostByDay = components["schemas"]["CostByDay"];
export type CostByModel = components["schemas"]["CostByModel"];
export type CostByUser = components["schemas"]["CostByUser"];
export type CostByTrigger = components["schemas"]["CostByTrigger"];
export type CostTotals = components["schemas"]["CostTotals"];

export type RunSummary = components["schemas"]["RunSummary"];
export type RunTrace = components["schemas"]["RunTrace"];
export type RunsPage = components["schemas"]["PageResponse_RunSummary_"];

export type ConversationSummary = components["schemas"]["ConversationSummary"];
export type ConversationDetail = components["schemas"]["ConversationDetail"];
export type ConversationsPage = components["schemas"]["PageResponse_ConversationSummary_"];

export type AuditEntry = components["schemas"]["AuditEntry"];
export type AuditPage = components["schemas"]["PageResponse_AuditEntry_"];

export type UserSummary = components["schemas"]["UserSummary"];
export type UsersPage = components["schemas"]["PageResponse_UserSummary_"];
export type UserPatch = components["schemas"]["UserPatch"];
export type UserPatchResult = components["schemas"]["UserPatchResult"];

export type LessonSummary = components["schemas"]["LessonSummary"];
export type LessonsPage = components["schemas"]["PageResponse_LessonSummary_"];
export type LessonPatch = components["schemas"]["LessonPatch"];
export type LessonDeleteResult = components["schemas"]["LessonDeleteResult"];

export type ApprovalResponse = components["schemas"]["ApprovalResponse"];
export type DecideRequest = components["schemas"]["DecideRequest"];

export type IncidentDossier = components["schemas"]["IncidentDossier"];

/**
 * A raw frame off `GET /api/admin/runs/stream`. Not one of the generated
 * schemas -- the operation's response is typed `unknown` in schema.d.ts
 * because FastAPI never declares a `response_model` for an SSE endpoint --
 * so this is hand-written from what app/tracing/store.py's `finalize_run`
 * actually publishes (the only producer onto broker.ADMIN_RUNS_CHANNEL):
 * `{"type": "run_finished", "id", "trigger", "status", "duration_ms",
 * "cost_usd"}`. Fields beyond `type`/`id` are optional here because
 * tests/test_admin_runs_stream.py also publishes bare `{"type", "id"}`
 * frames directly, and a frame the backend is willing to send must not
 * make this hook throw.
 */
export interface RunEvent {
  type: string;
  id: string;
  trigger?: string;
  status?: string;
  duration_ms?: number | null;
  cost_usd?: number | null;
}

/** Builds a `?a=1&b=2` query string, dropping undefined/null/empty entries
 * entirely rather than sending `?status=` or `?limit=undefined`. */
function query(params: Record<string, string | number | undefined | null>): string {
  const usable = Object.entries(params).filter(
    ([, value]) => value !== undefined && value !== null && value !== "",
  ) as [string, string | number][];
  if (usable.length === 0) return "";
  const search = new URLSearchParams(usable.map(([key, value]) => [key, String(value)]));
  return `?${search.toString()}`;
}

/** `GET /api/admin/overview`. */
export const adminOverview = () => apiFetch<Overview>("/api/admin/overview");

/** `GET /api/admin/costs`. */
export const adminCosts = () => apiFetch<Costs>("/api/admin/costs");

/**
 * `GET /api/admin/runs`. `limit`/`offset` are passed straight through
 * un-clamped -- queries.clamp_limit on the server answers an over-large
 * limit with the maximum page rather than a 422, so there is nothing for
 * this client to validate first.
 */
export const adminRuns = (params: { limit?: number; offset?: number } = {}) =>
  apiFetch<RunsPage>(`/api/admin/runs${query(params)}`);

/** `GET /api/admin/runs/{run_id}/trace`. */
export const adminTrace = (runId: string) => apiFetch<RunTrace>(`/api/admin/runs/${runId}/trace`);

/**
 * `GET /api/admin/runs/stream`. Never EventSource -- every route
 * authenticates from an Authorization header (app/deps.py), which
 * EventSource cannot send -- so apiStream carries the bearer token and
 * hands back the raw Response for readSse to parse. Consumed by
 * useRunStream, not called directly by a page.
 */
export const runsStream = (init: RequestInit) => apiStream("/api/admin/runs/stream", init);

/** `GET /api/admin/conversations`. */
export const adminConversations = (params: { q?: string; limit?: number; offset?: number } = {}) =>
  apiFetch<ConversationsPage>(`/api/admin/conversations${query(params)}`);

/** `GET /api/admin/conversations/{conversation_id}`. */
export const adminConversationDetail = (id: string) =>
  apiFetch<ConversationDetail>(`/api/admin/conversations/${id}`);

// A type alias, not an interface: `query()` below assigns this into a
// Record<string, ...> parameter, which TypeScript only permits without an
// explicit index signature for a plain object type literal -- an interface
// (open to declaration merging) is not structurally compatible the same
// way.
export type AdminAuditFilters = {
  actor_id?: string;
  action?: string;
  target_type?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
};

/**
 * `GET /api/admin/audit`. `since`/`until` are ISO-8601 strings the server
 * parses natively (app/admin/router.py) -- this module does no date
 * formatting of its own, it just forwards whatever the caller built.
 */
export const adminAudit = (filters: AdminAuditFilters = {}) =>
  apiFetch<AuditPage>(`/api/admin/audit${query(filters)}`);

/** `GET /api/admin/users`. */
export const adminUsers = (params: { limit?: number; offset?: number } = {}) =>
  apiFetch<UsersPage>(`/api/admin/users${query(params)}`);

/** `PATCH /api/admin/users/{user_id}`. Role and/or clearance only -- the
 * server ignores anything else in the body (app/admin/schemas.py's
 * UserPatch has `extra: "ignore"`), so this does not need to either. */
export const patchUser = (id: string, patch: UserPatch) =>
  apiFetch<UserPatchResult>(`/api/admin/users/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

/** `GET /api/admin/lessons`. */
export const adminLessons = (params: { limit?: number; offset?: number } = {}) =>
  apiFetch<LessonsPage>(`/api/admin/lessons${query(params)}`);

/** `PATCH /api/admin/lessons/{lesson_id}`. Content, title and/or status. */
export const patchLesson = (id: string, patch: LessonPatch) =>
  apiFetch<LessonSummary>(`/api/admin/lessons/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

/** `DELETE /api/admin/lessons/{lesson_id}`. Archives, never deletes the
 * row (app/admin/router.py) -- idempotent on an already-archived lesson. */
export const archiveLesson = (id: string) =>
  apiFetch<LessonDeleteResult>(`/api/admin/lessons/${id}`, { method: "DELETE" });

/** `GET /api/admin/approvals`, optionally filtered by status. */
export const adminApprovals = (status?: string) =>
  apiFetch<ApprovalResponse[]>(`/api/admin/approvals${query({ status })}`);

/** `POST /api/admin/approvals/{request_id}/decide`. */
export const decideApproval = (requestId: string, decision: DecideRequest) =>
  apiFetch<ApprovalResponse>(`/api/admin/approvals/${requestId}/decide`, {
    method: "POST",
    body: JSON.stringify(decision),
  });

/** `POST /api/admin/tickets/{ticket_id}/dossier`. A blocking model call --
 * no body to send, the ticket id in the path is the whole request. */
export const buildDossier = (ticketId: string) =>
  apiFetch<IncidentDossier>(`/api/admin/tickets/${ticketId}/dossier`, { method: "POST" });
