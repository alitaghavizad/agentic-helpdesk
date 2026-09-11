import type { ReactNode } from "react";

export type BadgeTone = "neutral" | "info" | "success" | "warning" | "danger";

const TONE_CLASSES: Record<BadgeTone, string> = {
  neutral: "bg-tone-neutral-bg text-tone-neutral-fg ring-tone-neutral-line",
  info: "bg-tone-info-bg text-tone-info-fg ring-tone-info-line",
  success: "bg-tone-success-bg text-tone-success-fg ring-tone-success-line",
  warning: "bg-tone-warning-bg text-tone-warning-fg ring-tone-warning-line",
  danger: "bg-tone-danger-bg text-tone-danger-fg ring-tone-danger-line",
};

/**
 * Small local primitive for status/role/priority pills. No component
 * library per the project's constraints -- every list screen from task 3
 * onward (ticket status, run status, dev-seed flags) renders through this.
 *
 * `dot` adds a leading marker for pills that report live state (a run that
 * is still going, a stream that is connected), where the colour alone reads
 * as decoration rather than as status.
 */
export function Badge({
  tone = "neutral",
  dot = false,
  pulse = false,
  children,
}: {
  tone?: BadgeTone;
  dot?: boolean;
  pulse?: boolean;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONE_CLASSES[tone]}`}
    >
      {dot && (
        <span
          aria-hidden="true"
          className={`size-1.5 rounded-full bg-current ${pulse ? "animate-pulse-soft" : ""}`}
        />
      )}
      {children}
    </span>
  );
}
