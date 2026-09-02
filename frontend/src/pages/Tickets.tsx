import { useState } from "react";
import type { ChangeEvent } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { ApiError } from "../api/client";
import * as tickets from "../api/endpoints/tickets";
import type { EditableTicketStatus, TicketDetail, TicketStatus, TicketSummary } from "../api/endpoints/tickets";
import { Badge } from "../components/Badge";
import type { BadgeTone } from "../components/Badge";
import { Table } from "../components/Table";
import type { Column } from "../components/Table";
import { Modal } from "../components/Modal";
import { StateBlock, describeError } from "../components/StateBlock";
import { dateTime } from "../lib/format";

const STATUS_TONE: Record<string, BadgeTone> = {
  open: "info",
  assigned: "info",
  in_progress: "warning",
  resolved: "success",
  closed: "neutral",
  escalated: "danger",
};

const PRIORITY_TONE: Record<string, BadgeTone> = {
  low: "neutral",
  medium: "info",
  high: "warning",
  urgent: "danger",
};

const STATUS_FILTER_OPTIONS: TicketStatus[] = ["open", "assigned", "in_progress", "resolved", "closed", "escalated"];

// "resolved" deliberately excluded: PATCH must never send it (see
// api/endpoints/tickets.ts's EditableTicketStatus) -- POST /resolve is the
// only path there, routed through the Resolve modal below instead.
const EDITABLE_STATUSES: EditableTicketStatus[] = ["open", "assigned", "in_progress", "closed", "escalated"];

function ticketsQueryKey(status: string) {
  return ["tickets", status] as const;
}
function ticketQueryKey(id: string) {
  return ["ticket", id] as const;
}

function errorDetail(error: unknown): string {
  return error instanceof ApiError ? error.detail : "Something went wrong. Please try again.";
}

/**
 * The Resolve modal's own button enablement: a resolution is what Phase 9's
 * learning loop later reads, so an empty (or whitespace-only) one must not
 * be submittable.
 */
function ResolveModal({
  ticketNumber, submitting, onCancel, onSubmit,
}: {
  ticketNumber: string;
  submitting: boolean;
  onCancel: () => void;
  onSubmit: (resolution: string) => void;
}) {
  const [resolution, setResolution] = useState("");
  const canSubmit = resolution.trim().length > 0 && !submitting;

  return (
    <Modal title={`Resolve ${ticketNumber}`} onClose={onCancel}>
      <label htmlFor="resolution" className="mb-1 block text-xs font-medium text-slate-700">
        Resolution
      </label>
      <textarea
        id="resolution"
        aria-label="Resolution"
        value={resolution}
        onChange={(event) => setResolution(event.target.value)}
        rows={4}
        className="mb-3 w-full rounded border border-slate-300 px-2 py-1 text-sm"
      />
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="rounded px-3 py-1.5 text-sm text-slate-600">
          Cancel
        </button>
        <button
          type="button"
          disabled={!canSubmit}
          onClick={() => onSubmit(resolution.trim())}
          className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "Resolving…" : "Resolve"}
        </button>
      </div>
    </Modal>
  );
}

/**
 * Status dropdown + Resolve button for one ticket -- shared by both the list
 * table's per-row actions column and the single-ticket view.
 *
 * Rendered only for helpdesk/admin: the backend enforces the same gate on
 * PATCH and POST /resolve (spec 14), but showing these controls to an
 * employee or guest would be a lie about what they can actually do, since
 * every attempt would 403.
 *
 * Neither mutation applies an optimistic update -- the select's `value` is
 * bound straight to the query cache's current status, so a failed PATCH
 * leaves the row showing exactly what it showed before the attempt, with
 * only an error message added.
 */
function TicketControls({
  ticket, isStaff,
}: {
  ticket: TicketSummary | TicketDetail;
  isStaff: boolean;
}) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [resolveOpen, setResolveOpen] = useState(false);

  function applyUpdated(updated: TicketDetail) {
    queryClient.setQueriesData<TicketSummary[]>({ queryKey: ["tickets"] }, (old) =>
      old?.map((row) =>
        row.id === updated.id
          ? { ...row, status: updated.status, priority: updated.priority, assignee_helpdesk_ref: updated.assignee_helpdesk_ref }
          : row,
      ),
    );
    queryClient.setQueryData(ticketQueryKey(updated.id), updated);
  }

  const statusMutation = useMutation({
    mutationFn: (status: EditableTicketStatus) => tickets.updateTicket(ticket.id, { status }),
    onSuccess: (updated) => {
      setError(null);
      applyUpdated(updated);
    },
    onError: (mutationError) => setError(errorDetail(mutationError)),
  });

  const resolveMutation = useMutation({
    mutationFn: (resolution: string) => tickets.resolveTicket(ticket.id, resolution),
    onSuccess: (updated) => {
      setError(null);
      applyUpdated(updated);
      setResolveOpen(false);
    },
    onError: (mutationError) => setError(errorDetail(mutationError)),
  });

  if (!isStaff) return null;

  function handleStatusChange(event: ChangeEvent<HTMLSelectElement>) {
    statusMutation.mutate(event.target.value as EditableTicketStatus);
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <select
          aria-label={`Status for ${ticket.ticket_number}`}
          value={ticket.status}
          onChange={handleStatusChange}
          disabled={statusMutation.isPending}
          className="rounded border border-slate-300 px-2 py-1 text-xs"
        >
          {/* Present only so a ticket that is ALREADY resolved renders its
              real value instead of silently falling back to the first
              option -- it is never one of the choices PATCH can send. */}
          {ticket.status === "resolved" && <option value="resolved">resolved</option>}
          {EDITABLE_STATUSES.map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => setResolveOpen(true)}
          className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
        >
          Resolve
        </button>
      </div>
      {error && (
        <p role="alert" className="text-xs text-red-700">
          {error}
        </p>
      )}
      {resolveOpen && (
        <ResolveModal
          ticketNumber={ticket.ticket_number}
          submitting={resolveMutation.isPending}
          onCancel={() => setResolveOpen(false)}
          onSubmit={(resolution) => resolveMutation.mutate(resolution)}
        />
      )}
    </div>
  );
}

function TicketDetailView({ ticket, isStaff }: { ticket: TicketDetail; isStaff: boolean }) {
  return (
    <div className="space-y-3 rounded border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm text-slate-500">{ticket.ticket_number}</span>
        <Badge tone={STATUS_TONE[ticket.status] ?? "neutral"}>{ticket.status}</Badge>
        <Badge tone={PRIORITY_TONE[ticket.priority] ?? "neutral"}>{ticket.priority}</Badge>
      </div>
      <h1 className="text-lg font-semibold text-slate-900">{ticket.title}</h1>
      <p className="whitespace-pre-wrap text-sm text-slate-700">{ticket.body}</p>
      <p className="text-xs text-slate-500">Assignee: {ticket.assignee_helpdesk_ref || "—"}</p>
      <p className="text-xs text-slate-500">Created: {dateTime(ticket.created_at)}</p>
      {ticket.resolution && (
        <p className="rounded bg-emerald-50 p-2 text-sm text-emerald-800">Resolution: {ticket.resolution}</p>
      )}
      <TicketControls ticket={ticket} isStaff={isStaff} />
    </div>
  );
}

export function Tickets() {
  const { principal } = useAuth();
  const { id } = useParams();
  const [statusFilter, setStatusFilter] = useState<TicketStatus | "">("");
  const isStaff = principal?.role === "admin" || principal?.role === "helpdesk";

  const listQuery = useQuery({
    queryKey: ticketsQueryKey(statusFilter),
    queryFn: () => tickets.listTickets(statusFilter || undefined),
    enabled: id === undefined,
  });

  const detailQuery = useQuery({
    queryKey: ticketQueryKey(id ?? ""),
    queryFn: () => tickets.getTicket(id as string),
    enabled: id !== undefined,
  });

  if (id !== undefined) {
    return (
      <div className="space-y-4">
        {detailQuery.isLoading ? (
          <StateBlock status="loading" />
        ) : detailQuery.isError ? (
          <StateBlock status="error" message={describeError(detailQuery.error)} />
        ) : detailQuery.data ? (
          <TicketDetailView ticket={detailQuery.data} isStaff={isStaff} />
        ) : null}
      </div>
    );
  }

  const rows = listQuery.data ?? [];
  const columns: Column<TicketSummary>[] = [
    { key: "number", header: "Number", render: (row) => <span className="font-mono text-xs">{row.ticket_number}</span> },
    { key: "title", header: "Title", render: (row) => row.title },
    {
      key: "status",
      header: "Status",
      render: (row) => <Badge tone={STATUS_TONE[row.status] ?? "neutral"}>{row.status}</Badge>,
    },
    {
      key: "priority",
      header: "Priority",
      render: (row) => <Badge tone={PRIORITY_TONE[row.priority] ?? "neutral"}>{row.priority}</Badge>,
    },
    { key: "assignee", header: "Assignee", render: (row) => row.assignee_helpdesk_ref || "—" },
    { key: "created", header: "Created", render: (row) => dateTime(row.created_at) },
    { key: "actions", header: "Actions", render: (row) => <TicketControls ticket={row} isStaff={isStaff} /> },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-900">Tickets</h1>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          Status
          <select
            aria-label="Filter by status"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value as TicketStatus | "")}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">All</option>
            {STATUS_FILTER_OPTIONS.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
        </label>
      </div>

      {listQuery.isLoading ? (
        <StateBlock status="loading" />
      ) : listQuery.isError ? (
        <StateBlock status="error" message={describeError(listQuery.error)} />
      ) : rows.length === 0 ? (
        <StateBlock status="empty" emptyLabel="No tickets to show." />
      ) : (
        <Table columns={columns} rows={rows} rowKey={(row) => row.id} />
      )}
    </div>
  );
}
