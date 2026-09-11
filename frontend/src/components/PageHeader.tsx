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
    <div className="mb-5 flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {description && <p className="mt-1 text-sm text-ink-3">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
