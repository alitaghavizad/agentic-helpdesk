import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { landingFor, useAuth } from "../auth/AuthContext";
import { Icon } from "../components/Icon";
import type { IconName } from "../components/Icon";
import { Spinner } from "../components/Icon";

type Tab = "credentials" | "guest";

const TAB_BUTTON = "-mb-px border-b-2 px-1 py-2.5 text-sm font-medium transition";

const HIGHLIGHTS: Array<{ icon: IconName; title: string; body: string }> = [
  { icon: "message", title: "Answers with receipts", body: "Every reply cites the internal docs it was drawn from." },
  { icon: "ticket", title: "Tickets that route themselves", body: "Requests reach the specialist whose skills actually match." },
  { icon: "shield", title: "A human signs off", body: "Privileged actions wait for an administrator's approval." },
];

export function Login() {
  const { login, loginAsGuest } = useAuth();
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("credentials");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function selectTab(next: Tab) {
    setTab(next);
    setError(null);
  }

  async function handleCredentials(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const principal = await login(username, password);
      navigate(landingFor(principal), { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Sign-in failed. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleGuest(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const principal = await loginAsGuest(name, email);
      navigate(landingFor(principal), { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not start a guest session. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  function tabClass(target: Tab) {
    return `${TAB_BUTTON} ${
      tab === target
        ? "border-brand text-ink"
        : "border-transparent text-ink-3 hover:border-line-strong hover:text-ink-2"
    }`;
  }

  return (
    <div className="grid min-h-screen bg-canvas lg:grid-cols-[1.1fr_1fr]">
      {/* Brand panel. Hidden below `lg` rather than stacked above the form:
          on a phone it would push the actual sign-in fields below the fold. */}
      <aside className="relative hidden overflow-hidden bg-brand p-12 text-on-brand lg:flex lg:flex-col lg:justify-between">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -top-24 -right-20 size-[28rem] rounded-full bg-white/10 blur-3xl"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -bottom-32 -left-16 size-[24rem] rounded-full bg-black/10 blur-3xl"
        />

        <div className="relative flex items-center gap-2.5">
          <span className="grid size-9 place-items-center rounded-xl bg-white/15 backdrop-blur">
            <Icon name="sparkles" className="size-5" />
          </span>
          <span className="text-base font-semibold tracking-tight">Agentic Helpdesk</span>
        </div>

        <div className="relative max-w-md">
          <h2 className="text-3xl leading-tight font-semibold tracking-tight text-balance">
            IT support that reads the docs so nobody has to.
          </h2>
          <ul className="mt-8 space-y-5">
            {HIGHLIGHTS.map((item) => (
              <li key={item.title} className="flex gap-3.5">
                <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-white/15 backdrop-blur">
                  <Icon name={item.icon} className="size-4" />
                </span>
                <span>
                  <span className="block text-sm font-semibold">{item.title}</span>
                  <span className="block text-sm text-on-brand/75">{item.body}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>

        <p className="relative text-xs text-on-brand/60">
          Northstar internal systems · authorised use only
        </p>
      </aside>

      <main className="flex items-center justify-center px-4 py-12 sm:px-8">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <span className="grid size-9 place-items-center rounded-xl bg-brand text-on-brand shadow-ambient">
              <Icon name="sparkles" className="size-5" />
            </span>
            <span className="text-base font-semibold tracking-tight text-ink">Agentic Helpdesk</span>
          </div>

          <h1 className="text-2xl font-semibold tracking-tight text-ink">Welcome back</h1>
          <p className="mt-1.5 mb-7 text-sm text-ink-3">
            Sign in with your Northstar account, or continue as a guest.
          </p>

          <div role="tablist" aria-label="Sign-in method" className="mb-6 flex gap-6 border-b border-line">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "credentials"}
              className={tabClass("credentials")}
              onClick={() => selectTab("credentials")}
            >
              Sign in
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "guest"}
              className={tabClass("guest")}
              onClick={() => selectTab("guest")}
            >
              Guest
            </button>
          </div>

          {error && (
            <p
              role="alert"
              className="mb-5 flex items-start gap-2 rounded-lg border border-tone-danger-line bg-tone-danger-bg px-3 py-2.5 text-sm font-medium text-tone-danger-fg"
            >
              <Icon name="alert" className="mt-0.5 size-4 shrink-0" />
              {error}
            </p>
          )}

          {tab === "credentials" ? (
            <form onSubmit={handleCredentials} className="space-y-5" aria-label="Sign in">
              <div>
                <label htmlFor="login-username" className="field-label">Username</label>
                <input
                  id="login-username"
                  className="field-input"
                  placeholder="j.doe"
                  value={username}
                  autoComplete="username"
                  required
                  onChange={(event) => setUsername(event.target.value)}
                />
              </div>
              <div>
                <label htmlFor="login-password" className="field-label">Password</label>
                <input
                  id="login-password"
                  type="password"
                  className="field-input"
                  placeholder="••••••••"
                  value={password}
                  autoComplete="current-password"
                  required
                  onChange={(event) => setPassword(event.target.value)}
                />
              </div>
              <button type="submit" disabled={submitting} className="btn-primary w-full py-2.5">
                {submitting ? <Spinner className="size-4" /> : <Icon name="logout" className="size-4" />}
                {submitting ? "Signing in…" : "Sign in"}
              </button>
            </form>
          ) : (
            <form onSubmit={handleGuest} className="space-y-5" aria-label="Continue as guest">
              <p className="flex items-start gap-2 rounded-lg border border-line bg-surface-2 px-3 py-2.5 text-sm text-ink-2">
                <Icon name="clock" className="mt-0.5 size-4 shrink-0 text-ink-3" />
                This session ends when you close or reload the tab.
              </p>
              <div>
                <label htmlFor="guest-name" className="field-label">Name</label>
                <input
                  id="guest-name"
                  className="field-input"
                  placeholder="Dana Whitfield"
                  value={name}
                  autoComplete="name"
                  required
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
              <div>
                <label htmlFor="guest-email" className="field-label">Email</label>
                <input
                  id="guest-email"
                  type="email"
                  className="field-input"
                  placeholder="dana@example.com"
                  value={email}
                  autoComplete="email"
                  required
                  onChange={(event) => setEmail(event.target.value)}
                />
              </div>
              <button type="submit" disabled={submitting} className="btn-primary w-full py-2.5">
                {submitting ? <Spinner className="size-4" /> : <Icon name="message" className="size-4" />}
                {submitting ? "Joining…" : "Continue as guest"}
              </button>
            </form>
          )}
        </div>
      </main>
    </div>
  );
}
