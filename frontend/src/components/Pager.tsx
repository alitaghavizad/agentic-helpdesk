interface PagerProps {
  /** Row count before limit/offset -- `PageResponse.total`. */
  total: number;
  /**
   * The server's own page size -- `PageResponse.limit`, never a value
   * invented on the client. `queries.clamp_limit` (backend/app/admin/queries.py)
   * answers an over-large requested limit with the maximum page (200)
   * rather than a 422, so the limit a caller asked for and the limit a
   * response actually carries can differ. Every offset this component
   * emits is derived from THIS number, not a client-side constant, so
   * paging still lands on the right row even when the server clamped the
   * request.
   */
  limit: number;
  /** The current page's starting row -- `PageResponse.offset`. */
  offset: number;
  onChange: (offset: number) => void;
}

/**
 * Small local pagination primitive -- no component library per this
 * project's constraints. Task 11 is the first screen set with a dataset
 * large enough to need one (126 seeded user accounts).
 *
 * Renders nothing once everything already fits on one page (`total <=
 * limit`): a Previous/Next control with both ends permanently disabled
 * tells an admin nothing a plain row count does not, and it is one less
 * pair of dead buttons on every small list in the app.
 */
export function Pager({ total, limit, offset, onChange }: PagerProps) {
  if (total <= limit) return null;

  // Guards a pathological `limit <= 0` from a malformed response -- never
  // actually 0 or negative in practice (clamp_limit floors at 1) -- so
  // dividing by it below can't produce Infinity/NaN math.
  const safeLimit = Math.max(1, limit);
  const currentPage = Math.floor(offset / safeLimit) + 1;
  const totalPages = Math.max(1, Math.ceil(total / safeLimit));
  const start = total === 0 ? 0 : offset + 1;
  const end = Math.min(offset + safeLimit, total);
  const canPrev = offset > 0;
  const canNext = offset + safeLimit < total;

  return (
    <div className="flex items-center justify-between gap-4 text-sm text-slate-600">
      <span>
        Showing {start}–{end} of {total}
      </span>
      <div className="flex items-center gap-2">
        <button
          type="button"
          aria-label="Previous page"
          disabled={!canPrev}
          onClick={() => onChange(Math.max(0, offset - safeLimit))}
          className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Previous
        </button>
        <span className="text-xs text-slate-500">
          Page {currentPage} of {totalPages}
        </span>
        <button
          type="button"
          aria-label="Next page"
          disabled={!canNext}
          onClick={() => onChange(offset + safeLimit)}
          className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Next
        </button>
      </div>
    </div>
  );
}
