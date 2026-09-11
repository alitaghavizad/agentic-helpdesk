export type IconName =
  | "activity"
  | "alert"
  | "bell"
  | "check"
  | "chevron-left"
  | "chevron-right"
  | "clock"
  | "close"
  | "coins"
  | "gauge"
  | "inbox"
  | "lightbulb"
  | "logout"
  | "message"
  | "moon"
  | "paperclip"
  | "plus"
  | "send"
  | "shield"
  | "sparkles"
  | "sun"
  | "ticket"
  | "users";

/**
 * Inline stroke icons. Hand-drawn rather than pulled from an icon package on
 * purpose: this project carries no component library (D5), and a handful of
 * paths costs nothing next to a dependency that ships a thousand glyphs to
 * render the twenty this app actually uses.
 */
const PATHS: Record<IconName, string> = {
  activity: "M3 12h4l3 8 4-16 3 8h4",
  alert: "M12 9v4m0 4h.01M10.3 3.9 2.4 17.5A2 2 0 0 0 4.1 20.5h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z",
  bell: "M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0",
  check: "m20 6-11 11-5-5",
  "chevron-left": "m15 18-6-6 6-6",
  "chevron-right": "m9 18 6-6-6-6",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 7v5l3 2",
  close: "M18 6 6 18M6 6l12 12",
  coins: "M12 14c4.4 0 8-1.3 8-3s-3.6-3-8-3-8 1.3-8 3 3.6 3 8 3ZM4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6",
  gauge: "M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18ZM12 12l4-4",
  inbox: "M4 13h4l2 3h4l2-3h4M4 13l2.5-7.3A2 2 0 0 1 8.4 4h7.2a2 2 0 0 1 1.9 1.7L20 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-5Z",
  lightbulb: "M9 21h6M10 17h4M12 3a6 6 0 0 0-3.5 10.9c.6.4.9 1 .9 1.7v.4h5.2v-.4c0-.7.3-1.3.9-1.7A6 6 0 0 0 12 3Z",
  logout: "M15 17l5-5-5-5M20 12H9M12 20H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h6",
  message: "M21 12a8 8 0 0 1-8 8H7l-4 3V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8Z",
  moon: "M21 13.2A9 9 0 1 1 10.8 3a7 7 0 0 0 10.2 10.2Z",
  paperclip: "M21.2 11.3 12.5 20a5 5 0 0 1-7.1-7.1l8.8-8.7a3.3 3.3 0 1 1 4.7 4.7l-8.7 8.8a1.7 1.7 0 0 1-2.4-2.4l8.1-8",
  plus: "M12 5v14M5 12h14",
  send: "M21 3 3 10.5l7 3 3 7L21 3Z",
  shield: "M12 21s7-3.4 7-9V6l-7-3-7 3v6c0 5.6 7 9 7 9Z",
  sparkles: "M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8L12 3ZM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15Z",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10ZM12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  ticket: "M4 9V7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v2a3 3 0 0 0 0 6v2a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-2a3 3 0 0 0 0-6ZM14 5v14",
  users: "M16 20v-1.5a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4V20M9 10.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM22 20v-1.5a4 4 0 0 0-3-3.9M16 3.6a4 4 0 0 1 0 7.7",
};

export function Icon({ name, className = "size-4" }: { name: IconName; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      <path d={PATHS[name]} />
    </svg>
  );
}

/** The one animated icon -- a ring that spins for pending/loading states. */
export function Spinner({ className = "size-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false" className={`animate-spin ${className}`}>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth={2.5} opacity={0.2} />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" />
    </svg>
  );
}
