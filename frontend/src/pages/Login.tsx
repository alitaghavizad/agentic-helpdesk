import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { landingFor, useAuth } from "../auth/AuthContext";

type Tab = "credentials" | "guest";

const TAB_BUTTON = "px-3 py-2 text-sm font-medium border-b-2 -mb-px";
const FIELD_LABEL = "mb-1 block text-sm font-medium text-slate-700";
const FIELD_INPUT = "w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none";
const SUBMIT_BUTTON = "w-full rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50";

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

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-8 shadow-sm">
        <h1 className="mb-6 text-xl font-semibold text-slate-900">Agentic Helpdesk</h1>

        <div role="tablist" aria-label="Sign-in method" className="mb-6 flex gap-4 border-b border-slate-200">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "credentials"}
            className={`${TAB_BUTTON} ${tab === "credentials" ? "border-slate-900 text-slate-900" : "border-transparent text-slate-500"}`}
            onClick={() => selectTab("credentials")}
          >
            Sign in
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "guest"}
            className={`${TAB_BUTTON} ${tab === "guest" ? "border-slate-900 text-slate-900" : "border-transparent text-slate-500"}`}
            onClick={() => selectTab("guest")}
          >
            Guest
          </button>
        </div>

        {error && (
          <p role="alert" className="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </p>
        )}

        {tab === "credentials" ? (
          <form onSubmit={handleCredentials} className="space-y-4" aria-label="Sign in">
            <div>
              <label htmlFor="login-username" className={FIELD_LABEL}>Username</label>
              <input
                id="login-username"
                className={FIELD_INPUT}
                value={username}
                autoComplete="username"
                required
                onChange={(event) => setUsername(event.target.value)}
              />
            </div>
            <div>
              <label htmlFor="login-password" className={FIELD_LABEL}>Password</label>
              <input
                id="login-password"
                type="password"
                className={FIELD_INPUT}
                value={password}
                autoComplete="current-password"
                required
                onChange={(event) => setPassword(event.target.value)}
              />
            </div>
            <button type="submit" disabled={submitting} className={SUBMIT_BUTTON}>
              {submitting ? "Signing in…" : "Sign in"}
            </button>
          </form>
        ) : (
          <form onSubmit={handleGuest} className="space-y-4" aria-label="Continue as guest">
            <p className="text-sm text-slate-600">
              This session ends when you close or reload the tab.
            </p>
            <div>
              <label htmlFor="guest-name" className={FIELD_LABEL}>Name</label>
              <input
                id="guest-name"
                className={FIELD_INPUT}
                value={name}
                autoComplete="name"
                required
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div>
              <label htmlFor="guest-email" className={FIELD_LABEL}>Email</label>
              <input
                id="guest-email"
                type="email"
                className={FIELD_INPUT}
                value={email}
                autoComplete="email"
                required
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>
            <button type="submit" disabled={submitting} className={SUBMIT_BUTTON}>
              {submitting ? "Joining…" : "Continue as guest"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
