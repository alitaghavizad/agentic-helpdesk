import { useMutation, useQuery } from "@tanstack/react-query";
import * as tickets from "../../api/endpoints/tickets";
import type { TicketStatus, TicketSummary } from "../../api/endpoints/tickets";
import * as admin from "../../api/endpoints/admin";
import { StateBlock, describeError } from "../../components/StateBlock";
import { PageHeader } from "../../components/PageHeader";
import { DossierCard } from "../../components/DossierCard";
import { score } from "../../lib/format";

const STATUSES: TicketStatus[] = ["open", "assigned", "in_progress", "resolved", "closed", "escalated"];

const STATUS_LABEL: Record<TicketStatus, string> = {
  open: "Open",
  assigned: "Assigned",
  in_progress: "In progress",
  resolved: "Resolved",
  closed: "Closed",
  escalated: "Escalated",
};

/**
 * The routing decision the agent made for this ticket --
 * `matched_specialization`, `assignment_rationale`, `assignment_score` --
 * which is what an admin looking at this board is actually checking.
 *
 * Task 10 review (Phase 8b): this used to be a per-card `GET
 * /api/tickets/{id}` fetch, because the original `TicketSummary` didn't
 * carry these three fields. That made an N-ticket board issue N+1 requests
 * against an unpaginated, unfiltered-for-admin `GET /api/tickets`
 * (backend/app/tickets/scoping.py's `scope_tickets_query` returns every
 * row for an admin, and the endpoint has no limit) -- and with the
 * production `QueryClient`'s default `retry: 3`, a flaky backend turned
 * that into up to 4N requests. `TicketSummary` now carries all three
 * fields directly (backend/app/tickets/router.py's `serialize_summary`
 * reads them off the same `Ticket` row it already reads everything else
 * from, no extra query), so this reads straight off the ticket the board
 * already has -- one request for the whole board, not one per card.
 */
function RoutingDecision({ ticket }: { ticket: TicketSummary }) {
  return (
    <dl className="space-y-2 border-t border-line pt-2.5 text-xs">
      <div>
        <dt className="eyebrow">Specialization</dt>
        <dd className="mt-0.5 text-ink-2">{ticket.matched_specialization}</dd>
      </div>
      <div>
        <dt className="eyebrow">Rationale</dt>
        <dd className="mt-0.5 text-ink-2">{ticket.assignment_rationale}</dd>
      </div>
      <div className="flex items-baseline gap-2">
        <dt className="eyebrow">Score</dt>
        <dd className="font-medium text-ink tabular-nums">{score(ticket.assignment_score)}</dd>
      </div>
    </dl>
  );
}

/**
 * One ticket card: its routing decision plus the dossier generator.
 *
 * The dossier button's pending state is not gated by any client-side
 * timeout -- `admin.buildDossier` goes through the shared `apiFetch`, which
 * never applies one (api/client.ts). Phase 8a measured the real call at
 * 36.5s; this component's job is to stay in a pending state for however
 * long the server actually takes, with a note that a model call is what is
 * running, rather than reading as broken.
 *
 * `hasDossier` below is gated on `isSuccess`, not merely on `data` being
 * present, specifically so a failed *re*-generation cannot leave a
 * previous successful dossier's data sitting in `mutation.data` and
 * rendering alongside the new error: react-query flips `isSuccess` to
 * `false` the instant a fresh `mutate()` call starts (before the promise
 * even settles), so the stale `DossierCard` disappears the moment a
 * regenerate begins, not only once it fails. The dossier is
 * schema-validated server-side, so any failure is a real error and this
 * card must show only that error, never a stale dossier dressed up as the
 * current one.
 */
function TicketCard({ ticket }: { ticket: TicketSummary }) {
  const dossierMutation = useMutation({
    mutationFn: () => admin.buildDossier(ticket.id),
  });

  function handleGenerate() {
    dossierMutation.mutate();
  }

  const hasDossier = dossierMutation.isSuccess && dossierMutation.data !== undefined;

  return (
    <div className="card space-y-2 p-3.5">
      <span className="font-mono text-xs text-ink-3">{ticket.ticket_number}</span>
      <p className="text-sm font-medium text-ink">{ticket.title}</p>
      <p className="text-xs text-ink-3">Assignee: {ticket.assignee_helpdesk_ref || "—"}</p>

      <RoutingDecision ticket={ticket} />

      <div className="space-y-1 pt-1">
        <button
          type="button"
          disabled={dossierMutation.isPending}
          onClick={handleGenerate}
          className="btn-secondary px-2 py-1 text-xs"
        >
          {dossierMutation.isPending ? "Generating dossier…" : hasDossier ? "Regenerate dossier" : "Generate dossier"}
        </button>
        <p className="text-[11px] text-ink-3">
          Runs a live model call and can take up to a minute to return.
        </p>
        {dossierMutation.isPending && (
          <p role="status" className="text-xs text-ink-3">
            Generating dossier… this calls the model and can take under a minute. Please wait.
          </p>
        )}
        {dossierMutation.isError && (
          <p role="alert" className="text-xs text-tone-danger-fg">
            {describeError(dossierMutation.error)}
          </p>
        )}
      </div>

      {hasDossier && dossierMutation.data && <DossierCard dossier={dossierMutation.data} />}
    </div>
  );
}

/**
 * Design spec 5.2 / task-10 brief: the admin ticket board, grouped by
 * `TicketStatus`, each card showing the routing decision plus the
 * incident-dossier generator. Read-only (per the phase's progress notes --
 * status/priority/assignee editing lives on the non-admin `/tickets`
 * screen's `TicketControls`, not here).
 *
 * Fetches the full unfiltered list once (`GET /api/tickets` with no
 * `status`) and buckets client-side into the six fixed columns, rather than
 * issuing six separately-filtered requests -- an admin viewing this board
 * needs every column at once, not one status at a time.
 */
export function Tickets() {
  const listQuery = useQuery({
    queryKey: ["admin", "tickets"],
    queryFn: () => tickets.listTickets(),
  });

  const rows = listQuery.data ?? [];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Tickets"
        description="Every ticket in the system, with its routing decision and assignee."
      />

      {listQuery.isLoading ? (
        <StateBlock status="loading" />
      ) : listQuery.isError ? (
        <StateBlock status="error" message={describeError(listQuery.error)} />
      ) : rows.length === 0 ? (
        <StateBlock status="empty" emptyLabel="No tickets to show." />
      ) : (
        <div className="-mx-1 flex snap-x gap-4 overflow-x-auto px-1 pb-2">
          {STATUSES.map((status) => {
            const columnRows = rows.filter((row) => row.status === status);
            return (
              <div key={status} className="w-72 shrink-0 snap-start space-y-2">
                <h2 className="flex items-center gap-2 text-xs font-semibold tracking-wider text-ink-3 uppercase">
                  {STATUS_LABEL[status]}{" "}
                  <span className="rounded-full bg-surface-2 px-1.5 py-0.5 text-[11px] font-medium text-ink-2 tabular-nums">
                    {columnRows.length}
                  </span>
                </h2>
                <div className="space-y-2">
                  {columnRows.length === 0 ? (
                    <p className="text-xs text-ink-3">No tickets.</p>
                  ) : (
                    columnRows.map((ticket) => <TicketCard key={ticket.id} ticket={ticket} />)
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
