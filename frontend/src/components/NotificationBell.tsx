import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useNotifications } from "../hooks/useNotifications";
import type { Notification } from "../api/endpoints/notifications";
import { StateBlock } from "./StateBlock";

/**
 * Only a `link_type` this app has a real detail route for gets turned into a
 * link. `"ticket"` resolves to `/tickets/{link_id}` now that App.tsx routes
 * it (task 5's Tickets page, which also serves the by-id view) -- every
 * other `link_type` still renders as plain text until a task adds a route
 * for it too.
 */
function pathFor(notification: Notification): string | null {
  if (notification.link_type === "ticket" && notification.link_id) {
    return `/tickets/${notification.link_id}`;
  }
  return null;
}

/**
 * `created_at` is real on every row now (backend/app/notifications/router.py
 * populates it for the REST list the same way it always did for the stream),
 * so this renders the notification's actual creation time rather than a
 * client-side proxy for it.
 */
function relativeTime(iso: string | null): string {
  const then = iso ? new Date(iso).getTime() : NaN;
  if (Number.isNaN(then)) return "—";
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function NotificationBell() {
  const { items, unread, markRead } = useNotifications();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleClick(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  async function handleSelect(notification: Notification) {
    if (!notification.read) {
      try {
        await markRead(notification.id);
      } catch {
        // Best-effort: leave it marked unread rather than blocking navigation.
      }
    }
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-label={unread > 0 ? `Notifications, ${unread} unread` : "Notifications"}
        aria-expanded={open}
        className="relative rounded p-2 text-slate-600 hover:bg-slate-100"
      >
        <span aria-hidden="true" className="text-lg">🔔</span>
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-semibold text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-10 mt-2 w-80 rounded border border-slate-200 bg-white shadow-lg">
          <div className="border-b border-slate-200 px-3 py-2 text-sm font-semibold text-slate-900">
            Notifications
          </div>
          <div className="max-h-96 overflow-y-auto">
            {items.length === 0 ? (
              <StateBlock status="empty" emptyLabel="No notifications yet." />
            ) : (
              <ul>
                {items.map((notification) => {
                  const path = pathFor(notification);
                  const body = (
                    <div className="flex flex-col gap-0.5 px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className={`text-sm ${notification.read ? "font-normal text-slate-600" : "font-semibold text-slate-900"}`}>
                          {notification.title}
                        </span>
                        {!notification.read && (
                          <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full bg-blue-600" />
                        )}
                      </div>
                      <p className="text-sm text-slate-600">{notification.body}</p>
                      <span className="text-xs text-slate-400">{relativeTime(notification.created_at)}</span>
                    </div>
                  );
                  return (
                    <li key={notification.id} className="border-b border-slate-100 last:border-b-0 hover:bg-slate-50">
                      {path ? (
                        <Link to={path} onClick={() => handleSelect(notification)} className="block">
                          {body}
                        </Link>
                      ) : (
                        <button type="button" onClick={() => handleSelect(notification)} className="block w-full text-left">
                          {body}
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
