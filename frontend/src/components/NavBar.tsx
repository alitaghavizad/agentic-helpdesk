import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import type { Principal } from "../api/endpoints/auth";
import { Badge } from "./Badge";
import { NotificationBell } from "./NotificationBell";

const ADMIN_LINKS: Array<{ to: string; label: string }> = [
  { to: "/admin", label: "Overview" },
  { to: "/admin/conversations", label: "Conversations" },
  { to: "/admin/traces", label: "Traces" },
  { to: "/admin/approvals", label: "Approvals" },
  { to: "/admin/tickets", label: "Tickets" },
  { to: "/admin/users", label: "Users" },
  { to: "/admin/lessons", label: "Lessons" },
  { to: "/admin/audit", label: "Audit" },
  { to: "/admin/costs", label: "Costs" },
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

const LINK_CLASS = ({ isActive }: { isActive: boolean }) =>
  `rounded px-2 py-1 text-sm ${isActive ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`;

export function NavBar() {
  const { principal, logout } = useAuth();
  const navigate = useNavigate();

  if (!principal) return null;

  async function handleSignOut() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-4 px-4 py-3">
        <span className="text-sm font-semibold text-slate-900">Agentic Helpdesk</span>

        <nav className="flex flex-wrap items-center gap-1">
          <NavLink to="/chat" className={LINK_CLASS}>Chat</NavLink>
          <NavLink to="/tickets" className={LINK_CLASS}>Tickets</NavLink>
        </nav>

        {principal.role === "admin" && (
          <nav className="flex flex-wrap items-center gap-1 border-l border-slate-200 pl-4">
            {ADMIN_LINKS.map((link) => (
              <NavLink key={link.to} to={link.to} end={link.to === "/admin"} className={LINK_CLASS}>
                {link.label}
              </NavLink>
            ))}
          </nav>
        )}

        <div className="ml-auto flex items-center gap-3">
          {/* Guests have no notification feed (notifications.user_id is
              NOT NULL and a guest is not a row in `users`), so the bell
              itself is user-only rather than rendering permanently empty. */}
          {principal.kind === "user" && <NotificationBell />}
          <span className="text-sm text-slate-600">{identityLabel(principal)}</span>
          <Badge tone={principal.role === "admin" ? "info" : "neutral"}>{principal.role}</Badge>
          <button
            type="button"
            onClick={handleSignOut}
            className="rounded border border-slate-300 px-3 py-1 text-sm text-slate-700 hover:bg-slate-50"
          >
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
}
