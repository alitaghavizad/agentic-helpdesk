import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import type { Principal } from "../api/endpoints/auth";
import { Badge } from "./Badge";
import { Icon } from "./Icon";
import type { IconName } from "./Icon";
import { NotificationBell } from "./NotificationBell";
import { ThemeToggle } from "./ThemeToggle";

const ADMIN_LINKS: Array<{ to: string; label: string; icon: IconName }> = [
  { to: "/admin", label: "Overview", icon: "gauge" },
  { to: "/admin/conversations", label: "Conversations", icon: "message" },
  { to: "/admin/traces", label: "Traces", icon: "activity" },
  { to: "/admin/approvals", label: "Approvals", icon: "shield" },
  { to: "/admin/tickets", label: "Tickets", icon: "ticket" },
  { to: "/admin/users", label: "Users", icon: "users" },
  { to: "/admin/lessons", label: "Lessons", icon: "lightbulb" },
  { to: "/admin/audit", label: "Audit", icon: "clock" },
  { to: "/admin/costs", label: "Costs", icon: "coins" },
];

function identityLabel(principal: Principal): string {
  // full_name/username are populated for every principal that has them
  // (backend/app/auth/router.py): a real user's actual name, or a guest's
  // self-reported one. employee_ref/helpdesk_ref/user_id are a defensive
  // fallback only -- the seeded admin has neither ref and would otherwise
  // show a raw UUID where their name belongs.
  return (
    principal.full_name ??
    principal.username ??
    principal.employee_ref ??
    principal.helpdesk_ref ??
    principal.user_id ??
    principal.role
  );
}

/** First letters of the display name -- "Jane Doe" becomes "JD". */
function initials(label: string): string {
  const parts = label.trim().split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

const PRIMARY_LINK = ({ isActive }: { isActive: boolean }) =>
  `inline-flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm font-medium transition ${
    isActive
      ? "bg-brand-soft text-brand-ink"
      : "text-ink-2 hover:bg-surface-2 hover:text-ink"
  }`;

/**
 * Admin links get their own row and their own active treatment (an underline
 * rather than a filled pill) so the two navigation levels stay visually
 * distinct: at nine entries, styling them like the primary links made the
 * whole bar read as one undifferentiated list.
 */
const ADMIN_LINK = ({ isActive }: { isActive: boolean }) =>
  `inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 px-1 py-2.5 text-sm transition ${
    isActive
      ? "border-brand font-medium text-ink"
      : "border-transparent text-ink-3 hover:border-line-strong hover:text-ink-2"
  }`;

export function NavBar() {
  const { principal, logout } = useAuth();
  const navigate = useNavigate();

  if (!principal) return null;

  async function handleSignOut() {
    await logout();
    navigate("/login", { replace: true });
  }

  const label = identityLabel(principal);
  const isAdmin = principal.role === "admin";

  return (
    <header className="sticky top-0 z-10 border-b border-line bg-surface/85 backdrop-blur-md">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-5 gap-y-3 px-4 py-2.5 sm:px-6">
        <span className="flex items-center gap-2.5">
          <span
            aria-hidden="true"
            className="grid size-8 place-items-center rounded-lg bg-brand text-on-brand shadow-ambient"
          >
            <Icon name="sparkles" className="size-4.5" />
          </span>
          <span className="text-sm font-semibold tracking-tight text-ink">Agentic Helpdesk</span>
        </span>

        <nav aria-label="Main" className="flex flex-wrap items-center gap-1">
          <NavLink to="/chat" className={PRIMARY_LINK}>
            <Icon name="message" className="size-4" />
            Chat
          </NavLink>
          <NavLink to="/tickets" className={PRIMARY_LINK}>
            <Icon name="ticket" className="size-4" />
            Tickets
          </NavLink>
        </nav>

        <div className="ml-auto flex items-center gap-2">
          {/* Guests have no notification feed (notifications.user_id is
              NOT NULL and a guest is not a row in `users`), so the bell
              itself is user-only rather than rendering permanently empty. */}
          {principal.kind === "user" && <NotificationBell />}
          <ThemeToggle />

          <span className="mx-1 hidden h-6 w-px bg-line sm:block" />

          <span className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="grid size-7 place-items-center rounded-full bg-surface-3 text-[11px] font-semibold text-ink-2"
            >
              {initials(label)}
            </span>
            <span className="hidden text-sm font-medium text-ink sm:block">{label}</span>
          </span>
          <Badge tone={isAdmin ? "info" : "neutral"}>{principal.role}</Badge>

          <button
            type="button"
            onClick={handleSignOut}
            className="btn-ghost px-2"
            title="Sign out"
          >
            <Icon name="logout" className="size-4" />
            <span className="hidden sm:inline">Sign out</span>
          </button>
        </div>
      </div>

      {isAdmin && (
        <div className="border-t border-line/70 bg-canvas-accent/40">
          <nav
            aria-label="Admin"
            className="mx-auto flex max-w-7xl items-center gap-5 overflow-x-auto px-4 sm:px-6"
          >
            {ADMIN_LINKS.map((link) => (
              <NavLink key={link.to} to={link.to} end={link.to === "/admin"} className={ADMIN_LINK}>
                <Icon name={link.icon} className="size-3.5" />
                {link.label}
              </NavLink>
            ))}
          </nav>
        </div>
      )}
    </header>
  );
}
