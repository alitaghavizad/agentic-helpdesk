import type { ReactNode } from "react";

/**
 * The one page title treatment, so every screen in the app opens the same
 * way: an `h1`, an optional sentence of context under it, and a slot on the
 * right for whatever that screen's controls are (a filter, a live-status
 * pill, a refresh button).
 */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        <h1 className="page-title">{title}</h1>
        {/* `max-w-prose` caps the line at roughly 65 characters. Without it
            the description runs the full 80rem of the shell on a wide
            monitor, which is far past the length an eye tracks back from
            comfortably. */}
        {description && <p className="mt-1.5 max-w-prose text-sm text-ink-3 text-pretty">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
