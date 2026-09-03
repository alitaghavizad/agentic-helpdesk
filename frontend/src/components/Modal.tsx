import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

interface ModalProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
}

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Small local modal primitive -- no component library or Radix per this
 * project's constraints. A backdrop click or Escape closes it; a click
 * inside the panel does not (stopPropagation on the panel).
 *
 * Focus trap and restore -- D5's deferred call, revisited now that this
 * project actually has "the approvals modal" D5 named
 * (docs/superpowers/specs/2026-09-02-admin-panel-frontend-design.md):
 * src/pages/Admin/Approvals.tsx's decide flow, which confirms a real-world
 * side effect (send an email, grant system access) with no undo. Without a
 * trap, Tab past the dialog's last control lands on whatever the dimmed
 * backdrop still exposes -- on that exact screen, another approval row's
 * own Approve/Deny buttons. A keyboard user who does not notice the focus
 * ring moved, and presses Enter, has just decided a *different* privileged
 * request. That is not a cosmetic gap; it is the same category of mistake
 * the backend's `.with_for_update()` row lock and this component's
 * disabled-while-pending buttons both exist to prevent, just reached
 * through the keyboard instead of a double-click.
 *
 * Deliberately the smallest version of a trap: it re-queries the panel's
 * current focusable elements on every Tab (so content that appears after
 * open -- an inline error, say -- is included), wraps Tab/Shift+Tab at the
 * first/last element, and restores focus to whatever had it before the
 * dialog opened. It does not manage `aria-hidden` on background siblings
 * or handle content added outside a keydown (a full a11y library's job).
 * That is enough for every modal in this app -- Tickets' Resolve/Reassign,
 * Approvals' decide -- and Radix remains the answer, per D5, if a future
 * modal needs more than this.
 */
export function Modal({ title, onClose, children }: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    // Focus the panel itself (its `tabIndex={-1}` makes this a valid
    // programmatic target without adding it to the Tab order) rather than
    // guessing which descendant is the "right" first control -- that
    // descendant is often the header's Close button purely because it
    // comes first in markup, and defaulting focus onto it would make the
    // very next keystroke (Enter/Space) dismiss the dialog instead of
    // reaching whatever the caller actually wants attention on. From here,
    // the first Tab moves into the dialog's own first focusable child, per
    // ordinary tab order.
    panel?.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panel) return;

      const nodes = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
      if (nodes.length === 0) {
        // Nothing to tab to inside the dialog -- keep focus pinned on the
        // panel itself rather than letting it escape to the page behind.
        event.preventDefault();
        return;
      }
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      const active = document.activeElement;
      const atEdge = event.shiftKey ? active === first : active === last;
      // `!panel.contains(active)` covers focus that has already drifted
      // outside the panel (e.g. a prior render's stale reference) -- wrap
      // it back in rather than letting a second Tab compound the escape.
      if (atEdge || !panel.contains(active)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previouslyFocused?.focus?.();
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-20 flex items-center justify-center bg-slate-900/40 p-4"
      onClick={onClose}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
        className="w-full max-w-md rounded bg-white p-4 shadow-xl"
      >
        <div className="mb-3 flex items-center justify-between gap-4">
          <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded text-slate-400 hover:text-slate-600"
          >
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
