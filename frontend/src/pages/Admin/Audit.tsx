import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import * as admin from "../../api/endpoints/admin";
import type { AuditEntry } from "../../api/endpoints/admin";
import { StateBlock, describeError } from "../../components/StateBlock";
import { Table } from "../../components/Table";
import type { Column } from "../../components/Table";
import { JsonBlock } from "../../components/JsonBlock";
import { Pager } from "../../components/Pager";
import { dateTime } from "../../lib/format";

interface AuditFilters {
  actorId: string;
  action: string;
  targetType: string;
  since: string;
  until: string;
}

const EMPTY_FILTERS: AuditFilters = { actorId: "", action: "", targetType: "", since: "", until: "" };

function auditQueryKey(filters: AuditFilters, offset: number) {
  return ["admin", "audit", filters, offset] as const;
}

/** `<input type="date">` yields `YYYY-MM-DD` or `""`. The backend parses
 * `since`/`until` as real ISO-8601 datetimes and treats an offset-less
 * value as UTC (queries._as_utc) -- `toISOString()` on a Date built from a
 * bare date string is already midnight UTC, so this needs no timezone math
 * of its own, just turning "no date picked" into "no filter" instead of an
 * invalid-date query param. */
function toIso(dateInput: string): string | undefined {
  if (!dateInput) return undefined;
  const parsed = new Date(dateInput);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString();
}

function actorLabel(row: AuditEntry): string {
  // actor_id is nullable (a SYSTEM-actor row has none) -- fall back to just
  // the actor_type rather than rendering "system · null" or crashing.
  return row.actor_id ? `${row.actor_type} · ${row.actor_id}` : row.actor_type;
}

/**
 * Spec 15's Audit screen: "Filterable append-only log". Every filter is a
 * real `GET /api/admin/audit` query parameter (actor_id, action,
 * target_type, since, until) -- app/admin/queries.py's `list_audit` does
 * the filtering in SQL, not this component, so there is no client-side
 * filter logic to keep in sync with the server's.
 *
 * Changing any filter resets `offset` back to 0: a filter narrow enough to
 * put fewer rows before the current offset than the offset itself would
 * otherwise land the admin on a page past the end of their own filtered
 * result, looking exactly like an empty log.
 */
export function Audit() {
  const [filters, setFilters] = useState<AuditFilters>(EMPTY_FILTERS);
  const [offset, setOffset] = useState(0);

  function updateFilter<K extends keyof AuditFilters>(key: K, value: AuditFilters[K]) {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setOffset(0);
  }

  const listQuery = useQuery({
    queryKey: auditQueryKey(filters, offset),
    queryFn: () =>
      admin.adminAudit({
        actor_id: filters.actorId || undefined,
        action: filters.action || undefined,
        target_type: filters.targetType || undefined,
        since: toIso(filters.since),
        until: toIso(filters.until),
        offset,
      }),
  });

  const page = listQuery.data;
  const rows = page?.items ?? [];

  const columns: Column<AuditEntry>[] = [
    { key: "actor", header: "Actor", render: actorLabel },
    { key: "action", header: "Action", render: (row) => row.action },
    { key: "target", header: "Target", render: (row) => `${row.target_type} · ${row.target_id}` },
    { key: "ip", header: "IP address", render: (row) => row.ip_address ?? "—" },
    { key: "payload", header: "Payload", render: (row) => <JsonBlock label="Payload" value={row.payload} /> },
    { key: "created_at", header: "Timestamp", render: (row) => dateTime(row.created_at) },
  ];

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-slate-900">Audit log</h1>

      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col text-xs text-slate-600">
          Actor ID
          <input
            type="text"
            aria-label="Filter by actor"
            value={filters.actorId}
            onChange={(event) => updateFilter("actorId", event.target.value)}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="flex flex-col text-xs text-slate-600">
          Action
          <input
            type="text"
            aria-label="Filter by action"
            value={filters.action}
            onChange={(event) => updateFilter("action", event.target.value)}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="flex flex-col text-xs text-slate-600">
          Target type
          <input
            type="text"
            aria-label="Filter by target type"
            value={filters.targetType}
            onChange={(event) => updateFilter("targetType", event.target.value)}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="flex flex-col text-xs text-slate-600">
          Since
          <input
            type="date"
            aria-label="Filter by since date"
            value={filters.since}
            onChange={(event) => updateFilter("since", event.target.value)}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="flex flex-col text-xs text-slate-600">
          Until
          <input
            type="date"
            aria-label="Filter by until date"
            value={filters.until}
            onChange={(event) => updateFilter("until", event.target.value)}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
      </div>

      {listQuery.isLoading ? (
        <StateBlock status="loading" />
      ) : listQuery.isError ? (
        <StateBlock status="error" message={describeError(listQuery.error)} />
      ) : rows.length === 0 ? (
        <StateBlock status="empty" emptyLabel="No matching entries." />
      ) : (
        <>
          <Table columns={columns} rows={rows} rowKey={(row) => row.id} />
          {page && <Pager total={page.total} limit={page.limit} offset={page.offset} onChange={setOffset} />}
        </>
      )}
    </div>
  );
}
