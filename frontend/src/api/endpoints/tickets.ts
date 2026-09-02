import { apiFetch } from "../client";
import type { components } from "../schema";

export type TicketSummary = components["schemas"]["TicketSummary"];
export type TicketDetail = components["schemas"]["TicketDetail"];
export type TicketStatus = components["schemas"]["TicketStatus"];
export type TicketPriority = components["schemas"]["TicketPriority"];

/**
 * The one status PATCH must never carry. backend/app/tickets/router.py's
 * update_ticket rejects `status: "resolved"` outright (400) -- `POST
 * /{id}/resolve` exists specifically to guarantee a non-empty resolution
 * plus attribution, and that resolution text is what Phase 9's learning loop
 * later reads. Typing the PATCH payload's status as this narrowed union
 * (rather than the full `TicketStatus`) means the status dropdown this
 * module feeds cannot even offer "resolved" as an option, so the UI cannot
 * regress into sending the one value the server exists to refuse.
 */
export type EditableTicketStatus = Exclude<TicketStatus, "resolved">;

export interface TicketPatch {
  status?: EditableTicketStatus;
  priority?: TicketPriority;
  assignee_helpdesk_ref?: string;
  reassignment_rationale?: string;
}

/**
 * `GET /api/tickets`, scoped server-side by role
 * (backend/app/tickets/scoping.py): admin sees all, helpdesk sees only
 * tickets assigned to their `helpdesk_ref`, an employee sees only tickets
 * they requested, a guest sees only tickets matching their email. This
 * client applies no filtering of its own -- whatever comes back is exactly
 * what the caller may see.
 */
export const listTickets = (status?: TicketStatus) =>
  apiFetch<TicketSummary[]>(`/api/tickets${status ? `?status=${encodeURIComponent(status)}` : ""}`);

/**
 * `GET /api/tickets/{id}`. A ticket the caller may not see 404s rather than
 * 403ing -- deliberately, so the endpoint never confirms which ticket ids
 * exist (backend/app/tickets/router.py's `load_readable_ticket`).
 */
export const getTicket = (id: string) => apiFetch<TicketDetail>(`/api/tickets/${id}`);

/**
 * `PATCH /api/tickets/{id}`. Requires helpdesk or admin -- the backend
 * re-checks regardless of what the UI shows.
 */
export const updateTicket = (id: string, patch: TicketPatch) =>
  apiFetch<TicketDetail>(`/api/tickets/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

/**
 * `POST /api/tickets/{id}/resolve` -- the only path that can move a ticket
 * to `resolved`, and the only one that requires (and records) a resolution.
 */
export const resolveTicket = (id: string, resolution: string) =>
  apiFetch<TicketDetail>(`/api/tickets/${id}/resolve`, {
    method: "POST",
    body: JSON.stringify({ resolution }),
  });
