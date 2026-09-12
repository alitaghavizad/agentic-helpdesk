import { ApiError } from "../api/client";
import { Icon } from "./Icon";

export type StateBlockStatus = "loading" | "empty" | "error";

/** How many placeholder rows a `loading` block draws. */
const SKELETON_ROWS = 5;

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
      // A skeleton in the shape of the list that is coming, rather than a
      // spinner centred in a dashed box. The spinner told the user only
      // that something was happening; this also tells them roughly what is
      // about to appear and holds the layout still when it does, so the
      // page does not jump on arrival.
      //
      // `aria-busy` plus the visually-hidden label is what carries this to a
      // screen reader -- the bars themselves are decorative and hidden, so
      // without the label the region would announce as empty.
      <div role="status" aria-busy="true" className="card overflow-hidden p-4">
        <span className="sr-only">{loadingLabel}</span>
        <div aria-hidden="true" className="space-y-3">
          {Array.from({ length: SKELETON_ROWS }, (_, row) => (
            <div key={row} className="flex items-center gap-3">
              <div className="skeleton size-8 shrink-0 rounded-md" />
              <div className="min-w-0 flex-1 space-y-2">
                {/* Staggered widths: rows of identical length read as a
                    striped pattern, not as text that is still loading. */}
                <div
                  className="skeleton h-3"
                  style={{ width: `${[62, 48, 71, 55, 44][row % 5]}%` }}
                />
                <div
                  className="skeleton h-2.5 opacity-70"
                  style={{ width: `${[34, 41, 28, 37, 31][row % 5]}%` }}
                />
              </div>
              <div className="skeleton h-5 w-16 shrink-0 rounded-full" />
            </div>
          ))}
        </div>
      </div>
    );
  }
  if (status === "error") {
    return (
      <div
        role="alert"
        className="flex flex-col items-center justify-center gap-2.5 rounded-card border border-tone-danger-line bg-tone-danger-bg p-8 text-center"
      >
        <span
          aria-hidden="true"
          className="grid size-9 place-items-center rounded-full bg-tone-danger-fg/10 text-tone-danger-fg"
        >
          <Icon name="alert" className="size-4.5" />
        </span>
        <p className="max-w-prose text-sm font-medium text-tone-danger-fg text-pretty">
          {message ?? "Something went wrong. Please try again."}
        </p>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-card border border-dashed border-line bg-surface-2/40 p-10 text-center">
      <span
        aria-hidden="true"
        className="grid size-11 place-items-center rounded-full bg-surface-3 text-ink-3"
      >
        <Icon name="inbox" className="size-5" />
      </span>
      <p className="max-w-prose text-sm text-ink-3 text-pretty">{message ?? emptyLabel}</p>
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
