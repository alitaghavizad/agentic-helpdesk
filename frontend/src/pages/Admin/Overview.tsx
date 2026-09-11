import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import * as admin from "../../api/endpoints/admin";
import { useRunStream } from "../../hooks/useRunStream";
import { StateBlock, describeError } from "../../components/StateBlock";
import { PageHeader } from "../../components/PageHeader";
import { Badge } from "../../components/Badge";
import { Icon } from "../../components/Icon";
import type { IconName } from "../../components/Icon";
import { RUN_STATUS_TONE } from "../../lib/runStatus";
import { usd } from "../../lib/format";

const OVERVIEW_QUERY_KEY = ["admin", "overview"] as const;

function Counter({
  label, value, icon, accent,
}: {
  label: string;
  value: string;
  icon: IconName;
  accent?: boolean;
}) {
  return (
    <div className="card p-4 transition hover:shadow-raised">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-medium text-ink-3">{label}</p>
        <span
          aria-hidden="true"
          className={`grid size-7 shrink-0 place-items-center rounded-lg ${
            accent ? "bg-brand-soft text-brand-ink" : "bg-surface-2 text-ink-3"
          }`}
        >
          <Icon name={icon} className="size-3.5" />
        </span>
      </div>
      <p className="mt-2 text-2xl font-semibold tracking-tight text-ink tabular-nums">{value}</p>
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
    <div>
      <PageHeader
        title="Overview"
        description="Today's agent activity across runs, spend, approvals and tickets."
        actions={
          <span role="status">
            <Badge tone={connected ? "success" : "warning"} dot pulse={connected}>
              {connected ? "Live" : "Reconnecting…"}
            </Badge>
          </span>
        }
      />

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <Counter label="Runs today" value={String(overview.runs_today)} icon="activity" accent />
        <Counter label="Spend today" value={usd(overview.spend_today)} icon="coins" />
        <Counter label="Pending approvals" value={String(overview.pending_approvals)} icon="shield" />
        <Counter label="Open tickets" value={String(overview.open_tickets)} icon="ticket" />
        <Counter
          label="Error rate, of today's completed runs"
          value={`${(overview.error_rate * 100).toFixed(1)}%`}
          icon="alert"
        />
      </div>

      <div className="card mt-4 p-4">
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-ink">
          <Icon name="activity" className="size-4 text-ink-3" />
          Live activity
        </h2>
        {events.length === 0 ? (
          <p className="py-6 text-center text-sm text-ink-3">No run activity yet.</p>
        ) : (
          <ul className="divide-y divide-line/60 text-sm">
            {events.map((event, index) => (
              <li
                key={`${event.id}-${events.length - index}`}
                className="flex animate-fade items-center justify-between gap-3 py-2"
              >
                <span className="truncate font-mono text-xs text-ink-3">{event.id}</span>
                <Badge tone={RUN_STATUS_TONE[event.status ?? ""] ?? "neutral"}>
                  {event.status ?? event.type}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
