import { useEffect, useMemo, useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import * as admin from "../../api/endpoints/admin";
import type { RunSummary } from "../../api/endpoints/admin";
import { useRunStream } from "../../hooks/useRunStream";
import { StateBlock, describeError } from "../../components/StateBlock";
import { Table } from "../../components/Table";
import type { Column } from "../../components/Table";
import { Badge } from "../../components/Badge";
import { SpanTree } from "../../components/SpanTree";
import { dateTime, duration, tokens, usd } from "../../lib/format";
import { RUN_STATUS_TONE } from "../../lib/runStatus";

const RUNS_QUERY_KEY = ["admin", "runs"] as const;

function traceQueryKey(runId: string) {
  return ["admin", "trace", runId] as const;
}

/**
 * `GET /api/admin/runs/stream` carries only what `finalize_run` publishes
 * (id/trigger/status/duration_ms/cost_usd -- see RunEvent's own doc
 * comment) -- never the token breakdown or timestamps a full RunSummary
 * row has. This fills in what a freshly-finished run's row can show before
 * the next full list refetch reconciles it, rather than waiting on that
 * refetch to show the run at all.
 */
function rowFromEvent(event: { id: string; trigger?: string; status?: string; duration_ms?: number | null; cost_usd?: number | null }): RunSummary {
  return {
    id: event.id,
    trigger: event.trigger ?? "",
    status: event.status ?? "running",
    started_at: null,
    duration_ms: event.duration_ms ?? null,
    cost_usd: event.cost_usd ?? null,
    llm_calls: null,
    tool_calls: null,
    error: null,
  };
}

function RunRow({
  run, selected, onSelect,
}: {
  run: RunSummary;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(run.id)}
      aria-current={selected}
      className={`rounded px-1.5 py-0.5 font-mono text-xs underline ${selected ? "text-slate-900" : "text-blue-700"}`}
    >
      {run.id}
    </button>
  );
}

/**
 * Spec 15's Traces screen: `GET /api/admin/runs` for the list, plus
 * `useRunStream` so a newly finished run appears without a manual refresh.
 * Selecting a run navigates to `/admin/traces/:runId` (shared with the
 * per-run deep links Chat.tsx's "View trace" now points at) and loads
 * `GET /api/admin/runs/{id}/trace` for the waterfall.
 */
export function Traces() {
  const { runId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // No Pager here, unlike Conversations/Users/Lessons/Audit -- this list is
  // continuously topped up by useRunStream below rather than a fixed page
  // an admin pages through, so a `limit` without an `offset` is the correct
  // shape for a live feed, not an inconsistency to "fix" into matching the
  // others.
  const runsQuery = useQuery({ queryKey: RUNS_QUERY_KEY, queryFn: () => admin.adminRuns({ limit: 50 }) });
  const { events, connected } = useRunStream();

  // Mirrors Overview: the stream drops a subscriber that falls behind and
  // closes rather than queueing (app/admin/router.py), so there is no
  // backlog to replay on reconnect -- a disconnect means the list may
  // already be stale, and re-reading it is the fix.
  const wasConnected = useRef(false);
  useEffect(() => {
    if (wasConnected.current && !connected) {
      queryClient.invalidateQueries({ queryKey: RUNS_QUERY_KEY });
    }
    wasConnected.current = connected;
  }, [connected, queryClient]);

  // Newly-finished runs from the stream, prepended ahead of the fetched
  // page -- but only ones the page doesn't already know about, so a
  // refetch that has already picked one up never shows it twice.
  const rows = useMemo(() => {
    const base = runsQuery.data?.items ?? [];
    const knownIds = new Set(base.map((run) => run.id));
    const fromStream: RunSummary[] = [];
    const seen = new Set<string>();
    for (const event of events) {
      if (knownIds.has(event.id) || seen.has(event.id)) continue;
      seen.add(event.id);
      fromStream.push(rowFromEvent(event));
    }
    return [...fromStream, ...base];
  }, [runsQuery.data, events]);

  const traceQuery = useQuery({
    queryKey: traceQueryKey(runId ?? ""),
    queryFn: () => admin.adminTrace(runId as string),
    enabled: runId !== undefined,
  });

  function selectRun(id: string) {
    navigate(`/admin/traces/${id}`);
  }

  const columns: Column<RunSummary>[] = [
    { key: "id", header: "Run", render: (row) => <RunRow run={row} selected={row.id === runId} onSelect={selectRun} /> },
    { key: "trigger", header: "Trigger", render: (row) => row.trigger },
    {
      key: "status",
      header: "Status",
      render: (row) => (
        <div>
          <Badge tone={RUN_STATUS_TONE[row.status] ?? "neutral"}>{row.status}</Badge>
          {/* A run that errored says why, right in the list -- an admin
              scanning for trouble should not have to open every row's
              trace just to learn there was a failure at all. */}
          {row.status === "error" && row.error && (
            <p className="mt-1 max-w-xs text-red-700">{row.error}</p>
          )}
        </div>
      ),
    },
    { key: "started", header: "Started", render: (row) => dateTime(row.started_at) },
    { key: "duration", header: "Duration", render: (row) => duration(row.duration_ms) },
    { key: "cost", header: "Cost", render: (row) => usd(row.cost_usd) },
    { key: "llm_calls", header: "LLM calls", render: (row) => tokens(row.llm_calls) },
    { key: "tool_calls", header: "Tool calls", render: (row) => tokens(row.tool_calls) },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-900">Traces</h1>
        <span
          role="status"
          className={`text-xs font-medium ${connected ? "text-emerald-600" : "text-amber-600"}`}
        >
          {connected ? "Live" : "Reconnecting…"}
        </span>
      </div>

      {runsQuery.isLoading ? (
        <StateBlock status="loading" />
      ) : runsQuery.isError ? (
        <StateBlock status="error" message={describeError(runsQuery.error)} />
      ) : rows.length === 0 ? (
        <StateBlock status="empty" emptyLabel="No runs recorded yet." />
      ) : (
        <Table columns={columns} rows={rows} rowKey={(row) => row.id} />
      )}

      {runId !== undefined && (
        <section className="rounded border border-slate-200 bg-white p-4">
          <h2 className="mb-3 text-sm font-semibold text-slate-900">Trace {runId}</h2>

          {traceQuery.isLoading ? (
            <StateBlock status="loading" />
          ) : traceQuery.isError ? (
            <StateBlock status="error" message={describeError(traceQuery.error)} />
          ) : !traceQuery.data ? (
            <StateBlock status="loading" />
          ) : (
            <>
              {/* `truncated: true` means the server dropped spans past its
                  cap (backend measured a single trace at 167,617 bytes) --
                  a silently short waterfall would read as a run that simply
                  stopped there, so this banner has to be impossible to miss. */}
              {traceQuery.data.truncated && (
                <p className="mb-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800">
                  This trace was truncated: only {tokens(traceQuery.data.span_count)} span
                  {traceQuery.data.span_count === 1 ? "" : "s"} of the full run are shown below.
                </p>
              )}

              <dl className="mb-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
                <div>
                  <dt className="uppercase tracking-wide text-slate-500">Trigger</dt>
                  <dd className="text-slate-800">{traceQuery.data.run.trigger}</dd>
                </div>
                <div>
                  <dt className="uppercase tracking-wide text-slate-500">Status</dt>
                  <dd>
                    <Badge tone={RUN_STATUS_TONE[traceQuery.data.run.status] ?? "neutral"}>{traceQuery.data.run.status}</Badge>
                  </dd>
                </div>
                <div>
                  <dt className="uppercase tracking-wide text-slate-500">Duration</dt>
                  <dd className="text-slate-800">{duration(traceQuery.data.run.duration_ms)}</dd>
                </div>
                <div>
                  <dt className="uppercase tracking-wide text-slate-500">Cost</dt>
                  <dd className="text-slate-800">{usd(traceQuery.data.run.cost_usd)}</dd>
                </div>
              </dl>

              {traceQuery.data.run.error && (
                <p role="alert" className="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                  {traceQuery.data.run.error}
                </p>
              )}

              <SpanTree roots={traceQuery.data.roots} totalMs={traceQuery.data.run.duration_ms ?? 0} />
            </>
          )}
        </section>
      )}
    </div>
  );
}
