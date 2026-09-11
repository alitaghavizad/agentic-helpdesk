import { ApiError } from "../api/client";
import { Icon, Spinner } from "./Icon";

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
      <div
        role="status"
        className="flex items-center justify-center gap-2.5 rounded-card border border-dashed border-line p-8 text-sm text-ink-3"
      >
        <Spinner className="size-4 text-brand" />
        {loadingLabel}
      </div>
    );
  }
  if (status === "error") {
    return (
      <div
        role="alert"
        className="flex items-center justify-center gap-2.5 rounded-card border border-tone-danger-line bg-tone-danger-bg p-8 text-center text-sm font-medium text-tone-danger-fg"
      >
        <Icon name="alert" className="size-4 shrink-0" />
        {message ?? "Something went wrong. Please try again."}
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-card border border-dashed border-line p-8 text-center text-sm text-ink-3">
      <Icon name="inbox" className="size-5 text-ink-3/70" />
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
