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
 *
 * `cache_hit_rate`'s label states its denominator explicitly (design spec
 * 5.3), the same rule Overview follows for `error_rate`: the fraction is
 * cache reads over EVERY prompt-side token processed -- input tokens, cache
 * reads, AND cache writes (app/admin/queries.py's `costs`) -- not just
 * reads plus fresh input. Omitting writes from the denominator would make a
 * workload that is establishing a cache (a conversation's first turn)
 * report a near-perfect hit rate while barely benefiting from caching at
 * all, so a bare "Cache hit rate" label would misdescribe what the number
 * means in exactly that case.
 *
 * `unpriced_calls` (per model, and summed into totals) exists because
 * `usd(null)` alone is inert defence: app/admin/queries.py's `costs`
 * coalesces an all-NULL model group's SUM to 0.0 rather than leaving it
 * NULL (a SUM cannot represent "unknown"), so a wholly-unpriced model's row
 * arrives as `cost_usd: 0.0` -- indistinguishable, on the wire, from a model
 * that genuinely cost nothing. The "By model" table's own column surfaces
 * which model that is; the banner below the totals surfaces that the grand
 * total itself is an understatement whenever any model has one.
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
    {
      key: "unpriced",
      header: "Unpriced calls",
      render: (row) =>
        row.unpriced_calls > 0 ? (
          <span className="font-medium text-amber-700">{row.unpriced_calls}</span>
        ) : (
          <span>0</span>
        ),
    },
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
        <Total
          label="Cache hit rate, of input + cache read + cache write tokens"
          value={pct(costs.totals.cache_hit_rate)}
        />
      </div>

      {costs.totals.unpriced_calls > 0 && (
        // Total cost above is a real number, not a placeholder -- but it is
        // an UNDERSTATEMENT whenever this is non-zero, since every unpriced
        // call folded a genuine $0 into that sum instead of its real
        // (unknown) cost. This is the one signal on the wire that says so.
        <p className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800">
          Total cost excludes {tokens(costs.totals.unpriced_calls)} unpriced call
          {costs.totals.unpriced_calls === 1 ? "" : "s"} with no known price -- the true total is
          higher than shown.
        </p>
      )}

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
