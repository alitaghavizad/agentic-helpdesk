import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import * as admin from "../../api/endpoints/admin";
import { useRunStream } from "../../hooks/useRunStream";
import { StateBlock, describeError } from "../../components/StateBlock";
import { usd } from "../../lib/format";

const OVERVIEW_QUERY_KEY = ["admin", "overview"] as const;

function Counter({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-slate-200 bg-white p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
    </div>
  );
}

/**
 * Spec 15's landing screen: five counters from `GET /api/admin/overview`
 * plus a live activity feed off `useRunStream`.
 *
 * `error_rate` is a fraction of today's COMPLETED runs, not of every run
 * (app/admin/queries.py's `overview`) -- an in-flight run has no outcome
 * yet to be wrong about, so counting it in the denominator would dilute the
 * rate exactly when a burst of traffic is in progress. The label says so
 * explicitly rather than just showing "Error rate", which would silently
 * misdescribe what the number means.
 */
export function Overview() {
  const queryClient = useQueryClient();
  // Design spec §6.3: 30s polling on the overview counters, on top of the
  // disconnect-triggered refetch below -- the stream only tells this
  // screen when a run finished, never when a ticket opened, a lesson
  // changed the pending-approvals count some other way, etc.
  const query = useQuery({ queryKey: OVERVIEW_QUERY_KEY, queryFn: admin.adminOverview, refetchInterval: 30_000 });
  const { events, connected } = useRunStream();

  // The backend drops a subscriber that falls too far behind and closes the
  // stream rather than queueing for it (app/admin/router.py's
  // admin_runs_stream) -- there is no backlog to replay on reconnect, so a
  // disconnect means these counters may already be stale. Re-reading them
  // is the fix, not assuming the feed's silence meant nothing changed.
  // Guarded by `wasConnected` so mounting (false -> ... -> true, the first
  // successful connect) never itself counts as a disconnect.
  const wasConnected = useRef(false);
  useEffect(() => {
    if (wasConnected.current && !connected) {
      queryClient.invalidateQueries({ queryKey: OVERVIEW_QUERY_KEY });
    }
    wasConnected.current = connected;
  }, [connected, queryClient]);

  if (query.isLoading) return <StateBlock status="loading" />;
  if (query.isError) return <StateBlock status="error" message={describeError(query.error)} />;
  const overview = query.data;
  // Mirrors Tickets.tsx's detailPhase: neither loading nor error, but no
  // data yet either, must still render a state block rather than fall
  // through to a blank page.
  if (!overview) return <StateBlock status="loading" />;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-900">Overview</h1>
        <span
          role="status"
          className={`text-xs font-medium ${connected ? "text-emerald-600" : "text-amber-600"}`}
        >
          {connected ? "Live" : "Reconnecting…"}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <Counter label="Runs today" value={String(overview.runs_today)} />
        <Counter label="Spend today" value={usd(overview.spend_today)} />
        <Counter label="Pending approvals" value={String(overview.pending_approvals)} />
        <Counter label="Open tickets" value={String(overview.open_tickets)} />
        <Counter
          label="Error rate, of today's completed runs"
          value={`${(overview.error_rate * 100).toFixed(1)}%`}
        />
      </div>

      <div className="rounded border border-slate-200 bg-white p-4">
        <h2 className="mb-2 text-sm font-semibold text-slate-900">Live activity</h2>
        {events.length === 0 ? (
          <p className="text-sm text-slate-500">No run activity yet.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {events.map((event, index) => (
              <li
                key={`${event.id}-${events.length - index}`}
                className="flex items-center justify-between border-b border-slate-100 py-1 text-slate-700 last:border-b-0"
              >
                <span className="font-mono text-xs text-slate-500">{event.id}</span>
                <span>{event.status ?? event.type}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
