export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "unpriced";
  if (Math.abs(value) >= 1) return `$${value.toFixed(2)}`;
  return `$${value.toFixed(6)}`;
}

export function tokens(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("en-US");
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  return `${minutes}m ${Math.round((ms % 60_000) / 1000)}s`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleString();
}

/**
 * Fixed two-decimal rendering for a bounded numeric score (e.g. the ticket
 * routing engine's `assignment_score`). A raw float off the wire can carry
 * binary-floating-point noise -- `0.8700000000000001` -- that is accurate
 * but unreadable; this renders the same value as `0.87`.
 */
export function score(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(2);
}
