import { ApiError } from "../api/client";

export type StateBlockStatus = "loading" | "empty" | "error";

interface StateBlockProps {
  status: StateBlockStatus;
  message?: string;
  loadingLabel?: string;
  emptyLabel?: string;
}

/**
 * The three states spec §6.5 requires of every list screen: loading, empty,
 * failed -- and never an empty table standing in for a failed request. Every
 * screen from task 3 onward renders through this instead of improvising its
 * own "no data" div.
 */
export function StateBlock({ status, message, loadingLabel = "Loading…", emptyLabel = "Nothing to show yet." }: StateBlockProps) {
  if (status === "loading") {
    return (
      <div role="status" className="flex justify-center rounded border border-dashed border-slate-200 p-8 text-sm text-slate-500">
        {loadingLabel}
      </div>
    );
  }
  if (status === "error") {
    return (
      <div role="alert" className="rounded border border-red-200 bg-red-50 p-8 text-center text-sm text-red-700">
        {message ?? "Something went wrong. Please try again."}
      </div>
    );
  }
  return (
    <div className="rounded border border-dashed border-slate-200 p-8 text-center text-sm text-slate-500">
      {message ?? emptyLabel}
    </div>
  );
}

/**
 * Turns a caught error into the sentence StateBlock should show. A 403 gets
 * spec §6.5's exact phrasing ("you do not have access to this") rather than
 * whatever detail FastAPI's permission dependency happens to raise.
 */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.status === 403 ? "You do not have access to this." : error.detail;
  }
  return "Something went wrong. Please try again.";
}
