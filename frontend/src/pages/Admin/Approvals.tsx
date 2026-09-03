import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as admin from "../../api/endpoints/admin";
import type { ApprovalResponse } from "../../api/endpoints/admin";
import { StateBlock, describeError } from "../../components/StateBlock";
import { Badge } from "../../components/Badge";
import type { BadgeTone } from "../../components/Badge";
import { Modal } from "../../components/Modal";
import { JsonBlock } from "../../components/JsonBlock";

type ApprovalsView = "pending" | "decided";

function approvalsQueryKey(view: ApprovalsView) {
  return ["admin", "approvals", view] as const;
}

const RISK_TONE: Record<string, BadgeTone> = {
  low: "neutral",
  medium: "warning",
  high: "danger",
};

const STATUS_TONE: Record<string, BadgeTone> = {
  pending: "info",
  approved: "success",
  denied: "danger",
  executed: "success",
  failed: "danger",
  expired: "neutral",
};

/**
 * The decision confirmation for one pending approval -- Approve or Deny,
 * with an optional note (`DecideRequest.note` defaults to "" server-side,
 * so an empty note is a valid submission, not a validation error).
 *
 * Uses the shared `Modal` primitive (D5's local-primitives decision, no
 * Radix) rather than firing the decision straight from the row's button.
 * This is the one place in the phase where that choice is genuinely load-
 * bearing: approving a privileged action -- the agent already justified it,
 * this is a human sign-off on `send_email`, `grant_system_access`, etc --
 * deserves an interstitial that shows what is about to happen and asks for
 * a confirming click, not a single click on a list row.
 */
function DecisionModal({
  requestNumber, approve, submitting, error, onCancel, onConfirm, restoreFocusFallback,
}: {
  requestNumber: string;
  approve: boolean;
  submitting: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: (note: string) => void;
  restoreFocusFallback: RefObject<HTMLElement | null>;
}) {
  const [note, setNote] = useState("");
  const verb = approve ? "Approve" : "Deny";

  return (
    <Modal title={`${verb} ${requestNumber}`} onClose={onCancel} restoreFocusFallback={restoreFocusFallback}>
      <label htmlFor="decision-note" className="mb-1 block text-xs font-medium text-slate-700">
        Note (optional)
      </label>
      <textarea
        id="decision-note"
        aria-label="Decision note"
        value={note}
        onChange={(event) => setNote(event.target.value)}
        rows={3}
        className="mb-3 w-full rounded border border-slate-300 px-2 py-1 text-sm"
      />
      {error && (
        <p role="alert" className="mb-2 text-xs text-red-700">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="rounded px-3 py-1.5 text-sm text-slate-600">
          Cancel
        </button>
        <button
          type="button"
          disabled={submitting}
          onClick={() => onConfirm(note.trim())}
          className={`rounded px-3 py-1.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50 ${
            approve ? "bg-emerald-700" : "bg-red-700"
          }`}
        >
          {submitting ? (approve ? "Approving…" : "Denying…") : `Confirm ${verb.toLowerCase()}`}
        </button>
      </div>
    </Modal>
  );
}

/**
 * One approval request: its justification, risk, full payload and a link
 * back to the conversation that produced it, plus (while pending) the
 * decide controls, or (once decided) its outcome.
 *
 * `decideMutation.isPending` gates both the Approve and Deny buttons
 * together, not just the one that was clicked. This is the client half of
 * Phase 6's TOCTOU fix: the backend's `.with_for_update()` row lock stops a
 * second concurrent `decide()` from double-executing, but by then a
 * double-click has already sent two real POSTs (two emails, in the
 * incident this fixed) -- the second one only turns into a harmless 409
 * instead of a harmful re-send. Not sending it in the first place is still
 * this component's job, and it holds regardless of which of the two
 * buttons -- or the modal's own confirm button -- gets clicked twice.
 */
function ApprovalCard({ approval }: { approval: ApprovalResponse }) {
  const queryClient = useQueryClient();
  const [decisionOpen, setDecisionOpen] = useState<"approve" | "deny" | null>(null);
  const [error, setError] = useState<string | null>(null);
  // A stable, persistent fallback focus target for the decide modal's
  // restore-on-close: a *successful* decide swaps this card's own
  // Approve/Deny buttons for its outcome in the same render that closes
  // the modal, so the button that opened it is gone from the document by
  // the time the modal's cleanup runs. This card's own container div is
  // not -- it is the one element in this row guaranteed to still be there
  // afterward, so it is where the admin's focus lands instead of falling
  // through to <body>. `tabIndex={-1}` makes it a valid programmatic
  // target without adding it to the page's Tab order.
  const cardRef = useRef<HTMLDivElement>(null);

  const decideMutation = useMutation({
    mutationFn: (input: { approve: boolean; note: string }) => admin.decideApproval(approval.id, input),
    onSuccess: (updated) => {
      setError(null);
      // Patches every cached approvals list (both the "pending" and
      // "decided" query keys share the ["admin", "approvals"] prefix) so
      // switching views afterward shows this row's real terminal state
      // without a refetch, and the pending view's own filter (below) drops
      // it the moment its status is no longer "pending".
      queryClient.setQueriesData<ApprovalResponse[]>({ queryKey: ["admin", "approvals"] }, (old) =>
        old?.map((row) => (row.id === updated.id ? updated : row)),
      );
      setDecisionOpen(null);
    },
    onError: (mutationError) => setError(describeError(mutationError)),
  });

  const isPending = approval.status === "pending";

  // Closes the second half of the decide-path focus story that Modal's own
  // restore (see Modal.tsx) cannot reach on its own: a successful decide
  // unmounts the modal in one render (where Modal's cleanup correctly
  // restores focus to the still-connected Approve/Deny trigger it opened
  // from) and THEN, in a separate, slightly later render -- once the query
  // cache update from `onSuccess` below actually reaches this component's
  // `approval` prop -- swaps those buttons out for the decided-info block.
  // Removing the now-focused trigger in that second render is what
  // actually kicks focus to `document.body` per the DOM's own focus
  // handling (a focused node removed from the document is not "restored"
  // anywhere by the browser). This effect catches exactly that: the
  // pending -> decided transition landing focus on `<body>`, and moves it
  // to this card instead, so the admin's focus ends up on the very outcome
  // they just produced rather than nowhere.
  const wasPending = useRef(isPending);
  useEffect(() => {
    if (wasPending.current && !isPending && document.activeElement === document.body) {
      cardRef.current?.focus();
    }
    wasPending.current = isPending;
  }, [isPending]);

  return (
    <div ref={cardRef} tabIndex={-1} className="space-y-3 rounded border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm text-slate-500">{approval.request_number}</span>
          <span className="text-sm font-medium text-slate-900">{approval.action_type}</span>
          <Badge tone={RISK_TONE[approval.risk_level] ?? "neutral"}>{approval.risk_level} risk</Badge>
        </div>
        <Badge tone={STATUS_TONE[approval.status] ?? "neutral"}>{approval.status}</Badge>
      </div>

      <p className="text-sm text-slate-700">{approval.justification}</p>
      <p className="text-sm italic text-slate-500">{approval.agent_summary}</p>

      <JsonBlock label="Action payload" value={approval.action_payload} />

      {/*
       * Links to the admin Conversations list, not a per-conversation
       * route -- Conversations.tsx (src/pages/Admin/Conversations.tsx)
       * selects a conversation entirely through its own component state,
       * set by clicking a row; App.tsx wires only `/admin/conversations`,
       * with no `:id` (unlike Tickets' `/tickets/:id` or Traces'
       * `/admin/traces/:runId`). Pointing this at
       * `/admin/conversations/{id}` would fall through App.tsx's `/admin/*`
       * catch-all straight to NotFound -- exactly the dead link an earlier
       * task in this phase was flagged for shipping. This link is real and
       * always resolves; it just does not deep-select, because the screen
       * it targets has no way to be told to.
       */}
      <Link to="/admin/conversations" className="inline-block text-sm text-blue-700 underline">
        View source conversation
      </Link>

      {isPending ? (
        <div className="flex flex-col gap-2">
          {error && (
            <p role="alert" className="text-xs text-red-700">
              {error}
            </p>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              disabled={decideMutation.isPending}
              onClick={() => setDecisionOpen("approve")}
              className="rounded bg-emerald-700 px-3 py-1.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              Approve
            </button>
            <button
              type="button"
              disabled={decideMutation.isPending}
              onClick={() => setDecisionOpen("deny")}
              className="rounded bg-red-700 px-3 py-1.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              Deny
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {approval.decision_note && <p className="text-sm text-slate-600">Note: {approval.decision_note}</p>}
          {approval.execution_result !== null && (
            <JsonBlock label="Execution result" value={approval.execution_result} />
          )}
        </div>
      )}

      {decisionOpen && (
        <DecisionModal
          requestNumber={approval.request_number}
          approve={decisionOpen === "approve"}
          submitting={decideMutation.isPending}
          error={error}
          onCancel={() => setDecisionOpen(null)}
          onConfirm={(note) => decideMutation.mutate({ approve: decisionOpen === "approve", note })}
          restoreFocusFallback={cardRef}
        />
      )}
    </div>
  );
}

/**
 * Spec 14's Approvals screen: "Pending queue with the agent's
 * justification, risk level, full payload, source conversation link, and
 * approve/deny with a note; decided items remain visible with their
 * execution result".
 *
 * The status filter has exactly two states because that is what the
 * backend actually supports: `GET /api/admin/approvals?status=` takes one
 * `ApprovalStatus` value or none at all (backend/app/approvals/service.py's
 * `list_for_admin`) -- there is no "decided" value in that enum. "Pending"
 * sends `?status=pending`, the narrow, fast, server-filtered query this
 * screen defaults to. "Decided" fetches the full unpaginated list (no
 * `status` param at all) and filters out `pending` rows here, because that
 * is the only way to ask for "everything that is not pending" against an
 * endpoint whose filter is a single exact-match enum.
 */
export function Approvals() {
  const [view, setView] = useState<ApprovalsView>("pending");

  const listQuery = useQuery({
    queryKey: approvalsQueryKey(view),
    queryFn: () => admin.adminApprovals(view === "pending" ? "pending" : undefined),
    // Design spec §6.3: 30s polling on the approvals queue. A pending
    // request an admin has not yet acted on can be decided by someone
    // else, or expire, without this tab doing anything to notice.
    refetchInterval: 30_000,
  });

  const rows = listQuery.data ?? [];
  // The pending view is NOT re-filtered to `status === "pending"` here.
  // `ApprovalCard`'s decide mutation patches this same cached array in
  // place (see its onSuccess below) rather than removing the row, so a
  // just-decided item -- including a `failed` one -- stays on screen with
  // its `execution_result` until the next refetch (the 30s interval above,
  // or a manual filter switch) replaces the array with the server's fresh
  // pending-only list. Filtering here would hide that outcome immediately,
  // forcing the admin to switch to "Decided" to see what their own click
  // just did -- exactly what the brief requires this screen not do.
  const displayed = view === "pending" ? rows : rows.filter((row) => row.status !== "pending");

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-lg font-semibold text-slate-900">Approvals</h1>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          Status
          <select
            aria-label="Filter by status"
            value={view}
            onChange={(event) => setView(event.target.value as ApprovalsView)}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="pending">Pending</option>
            <option value="decided">Decided</option>
          </select>
        </label>
      </div>

      {listQuery.isLoading ? (
        <StateBlock status="loading" />
      ) : listQuery.isError ? (
        <StateBlock status="error" message={describeError(listQuery.error)} />
      ) : displayed.length === 0 ? (
        <StateBlock
          status="empty"
          emptyLabel={view === "pending" ? "No approvals pending." : "No decided approvals yet."}
        />
      ) : (
        <div className="space-y-3">
          {displayed.map((approval) => (
            <ApprovalCard key={approval.id} approval={approval} />
          ))}
        </div>
      )}
    </div>
  );
}
