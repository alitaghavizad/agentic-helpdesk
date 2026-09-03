import { useQuery } from "@tanstack/react-query";
import * as admin from "../../api/endpoints/admin";
import type { CostByDay, CostByModel, CostByTrigger, CostByUser } from "../../api/endpoints/admin";
import { StateBlock, describeError } from "../../components/StateBlock";
import { Table } from "../../components/Table";
import type { Column } from "../../components/Table";
import { tokens, usd } from "../../lib/format";

const COSTS_QUERY_KEY = ["admin", "costs"] as const;

function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function Total({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-slate-200 bg-white p-3">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-semibold text-slate-900">{value}</p>
    </div>
  );
}

/**
 * No charting library, per this phase's constraints -- one `<div>` per day
 * whose width is a plain percentage of the largest day's spend. `max` is
 * computed against `cost_usd ?? 0` so a null (unpriced) day never poisons
 * the scale into treating everything as equally tall or NaN-wide.
 */
function DayBars({ rows }: { rows: CostByDay[] }) {
  const max = rows.reduce((running, row) => Math.max(running, row.cost_usd ?? 0), 0);
  return (
    <div className="space-y-1">
      {rows.map((row) => {
        const width = max > 0 ? ((row.cost_usd ?? 0) / max) * 100 : 0;
        return (
          <div key={row.day} className="flex items-center gap-2 text-xs">
            <span className="w-24 shrink-0 text-slate-500">{row.day}</span>
            <div className="h-3 flex-1 rounded bg-slate-100">
              <div
                role="img"
                aria-label={`${row.day}: ${usd(row.cost_usd)}`}
                className="h-3 rounded bg-slate-700"
                style={{ width: `${width}%` }}
              />
            </div>
            <span className="w-24 shrink-0 text-right text-slate-700">{usd(row.cost_usd)}</span>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Spec 15's Costs screen: four groupings (by day, model, user, trigger)
 * plus totals, from `GET /api/admin/costs`.
 *
 * Every dollar figure renders through `usd()`, never a raw template
 * literal -- a model absent from the pricing table stores its cost as NULL
 * (app/tracing/pricing.py's `cost_for` "never fabricates a number"), and
 * `usd(null)` is the one thing standing between that and a confidently
 * wrong "$0.00". CostByModel.cost_usd is typed as a plain `number` in the
 * generated schema (schema.d.ts has no `response_model` finer than that),
 * but `usd()` accepts `number | null | undefined` regardless, so a row that
 * actually arrives with `cost_usd: null` still renders "unpriced" rather
 * than crashing or defaulting to zero.
 */
export function Costs() {
  const query = useQuery({ queryKey: COSTS_QUERY_KEY, queryFn: admin.adminCosts });

  if (query.isLoading) return <StateBlock status="loading" />;
  if (query.isError) return <StateBlock status="error" message={describeError(query.error)} />;
  const costs = query.data;
  if (!costs) return <StateBlock status="loading" />;

  const modelColumns: Column<CostByModel>[] = [
    { key: "model", header: "Model", render: (row) => row.model },
    { key: "calls", header: "Calls", render: (row) => tokens(row.calls) },
    { key: "cost", header: "Cost", render: (row) => usd(row.cost_usd) },
  ];
  const userColumns: Column<CostByUser>[] = [
    { key: "user", header: "User", render: (row) => row.username },
    { key: "cost", header: "Cost", render: (row) => usd(row.cost_usd) },
  ];
  const triggerColumns: Column<CostByTrigger>[] = [
    { key: "trigger", header: "Trigger", render: (row) => row.trigger },
    { key: "runs", header: "Runs", render: (row) => tokens(row.runs) },
    { key: "cost", header: "Cost", render: (row) => usd(row.cost_usd) },
  ];

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold text-slate-900">Costs</h1>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Total label="Input tokens" value={tokens(costs.totals.input_tokens)} />
        <Total label="Output tokens" value={tokens(costs.totals.output_tokens)} />
        <Total label="Cache read tokens" value={tokens(costs.totals.cache_read_tokens)} />
        <Total label="Cache write tokens" value={tokens(costs.totals.cache_write_tokens)} />
        <Total label="Total cost" value={usd(costs.totals.cost_usd)} />
        <Total label="Cache hit rate" value={pct(costs.totals.cache_hit_rate)} />
      </div>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-900">Spend by day</h2>
        {costs.by_day.length === 0 ? (
          <StateBlock status="empty" emptyLabel="No spend recorded yet." />
        ) : (
          <DayBars rows={costs.by_day} />
        )}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-900">By model</h2>
        {costs.by_model.length === 0 ? (
          <StateBlock status="empty" emptyLabel="No model activity yet." />
        ) : (
          <Table columns={modelColumns} rows={costs.by_model} rowKey={(row) => row.model} />
        )}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-900">By user</h2>
        {costs.by_user.length === 0 ? (
          <StateBlock status="empty" emptyLabel="No user activity yet." />
        ) : (
          <Table columns={userColumns} rows={costs.by_user} rowKey={(row) => row.username} />
        )}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-900">By trigger</h2>
        {costs.by_trigger.length === 0 ? (
          <StateBlock status="empty" emptyLabel="No trigger activity yet." />
        ) : (
          <Table columns={triggerColumns} rows={costs.by_trigger} rowKey={(row) => row.trigger} />
        )}
      </section>
    </div>
  );
}
