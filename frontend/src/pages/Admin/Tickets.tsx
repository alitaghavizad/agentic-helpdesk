import { useMutation, useQuery } from "@tanstack/react-query";
import * as tickets from "../../api/endpoints/tickets";
import type { TicketStatus, TicketSummary } from "../../api/endpoints/tickets";
import * as admin from "../../api/endpoints/admin";
import { StateBlock, describeError } from "../../components/StateBlock";
import { DossierCard } from "../../components/DossierCard";

const STATUSES: TicketStatus[] = ["open", "assigned", "in_progress", "resolved", "closed", "escalated"];

const STATUS_LABEL: Record<TicketStatus, string> = {
  open: "Open",
  assigned: "Assigned",
  in_progress: "In progress",
  resolved: "Resolved",
  closed: "Closed",
  escalated: "Escalated",
};

function ticketDetailQueryKey(id: string) {
  return ["admin", "ticket-detail", id] as const;
}

/**
 * The routing decision the agent made for this ticket -- `matched_specialization`,
 * `assignment_rationale`, `assignment_score` -- which is what an admin looking
 * at this board is actually checking. `GET /api/tickets` (TicketSummary) does
 * not carry these three fields; only `GET /api/tickets/{id}` (TicketDetail)
 * does (backend/app/tickets/router.py). So each card fetches its own detail
 * rather than the board fetching one bulk list -- there is no bulk endpoint
 * that returns them, and this task adds no backend route.
 */
function RoutingDecision({ ticketId }: { ticketId: string }) {
  const detailQuery = useQuery({
    queryKey: ticketDetailQueryKey(ticketId),
    queryFn: () => tickets.getTicket(ticketId),
  });

  if (detailQuery.isLoading) {
    return <p className="text-xs text-slate-400">Loading routing details…</p>;
  }
  if (detailQuery.isError) {
    return (
      <p role="alert" className="text-xs text-red-700">
        Routing details unavailable: {describeError(detailQuery.error)}
      </p>
    );
  }
  const detail = detailQuery.data;
  if (!detail) return null;

  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-xs text-slate-600">
      <dt className="font-medium text-slate-500">Specialization</dt>
      <dd>{detail.matched_specialization}</dd>
      <dt className="font-medium text-slate-500">Rationale</dt>
      <dd>{detail.assignment_rationale}</dd>
      <dt className="font-medium text-slate-500">Score</dt>
      <dd>{detail.assignment_score}</dd>
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
    <div className="space-y-2 rounded border border-slate-200 bg-white p-3">
      <span className="font-mono text-xs text-slate-500">{ticket.ticket_number}</span>
      <p className="text-sm font-medium text-slate-900">{ticket.title}</p>
      <p className="text-xs text-slate-500">Assignee: {ticket.assignee_helpdesk_ref || "—"}</p>

      <RoutingDecision ticketId={ticket.id} />

      <div className="space-y-1 pt-1">
        <button
          type="button"
          disabled={dossierMutation.isPending}
          onClick={handleGenerate}
          className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {dossierMutation.isPending ? "Generating dossier…" : hasDossier ? "Regenerate dossier" : "Generate dossier"}
        </button>
        <p className="text-[11px] text-slate-400">
          Runs a live model call and can take up to a minute to return.
        </p>
        {dossierMutation.isPending && (
          <p role="status" className="text-xs text-slate-500">
            Generating dossier… this calls the model and can take under a minute. Please wait.
          </p>
        )}
        {dossierMutation.isError && (
          <p role="alert" className="text-xs text-red-700">
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
      <h1 className="text-lg font-semibold text-slate-900">Tickets</h1>

      {listQuery.isLoading ? (
        <StateBlock status="loading" />
      ) : listQuery.isError ? (
        <StateBlock status="error" message={describeError(listQuery.error)} />
      ) : rows.length === 0 ? (
        <StateBlock status="empty" emptyLabel="No tickets to show." />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          {STATUSES.map((status) => {
            const columnRows = rows.filter((row) => row.status === status);
            return (
              <div key={status} className="space-y-2">
                <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {STATUS_LABEL[status]} ({columnRows.length})
                </h2>
                <div className="space-y-2">
                  {columnRows.length === 0 ? (
                    <p className="text-xs text-slate-400">No tickets.</p>
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
