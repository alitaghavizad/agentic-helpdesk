import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { setAccessToken, setAuthFailureHandler } from "../api/client";
import * as auth from "../api/endpoints/auth";
import type { Principal } from "../api/endpoints/auth";

// Re-exported so consumers (RequireRole.test.tsx among them) can reference
// `Principal` off this module without a second import from api/endpoints/auth.
export type { Principal };

type Status = "loading" | "signed-in" | "signed-out";

interface AuthValue {
  status: Status;
  principal: Principal | null;
  login: (username: string, password: string) => Promise<Principal>;
  loginAsGuest: (name: string, email: string) => Promise<Principal>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function landingFor(principal: Principal): string {
  if (principal.role === "admin") return "/admin";
  if (principal.role === "helpdesk") return "/tickets";
  return "/chat";
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [principal, setPrincipal] = useState<Principal | null>(null);

  const clear = useCallback(() => {
    setAccessToken(null);
    setPrincipal(null);
    setStatus("signed-out");
  }, []);

  const adopt = useCallback(async (token: string) => {
    setAccessToken(token);
    const who = await auth.me();
    setPrincipal(who);
    setStatus("signed-in");
    return who;
  }, []);

  // Boot: the access token is memory-only, so a reload has none. The
  // httpOnly refresh cookie is the only path back into a session -- and
  // guests never receive one (app/auth/router.py issues it for kind ==
  // "user" only), so a guest reload correctly lands signed-out.
  useEffect(() => {
    setAuthFailureHandler(clear);
    let cancelled = false;
    (async () => {
      try {
        const { access_token } = await auth.refresh();
        if (!cancelled) await adopt(access_token);
      } catch {
        if (!cancelled) clear();
      }
    })();
    return () => { cancelled = true; };
  }, [adopt, clear]);

  const value = useMemo<AuthValue>(() => ({
    status,
    principal,
    login: async (username, password) => adopt((await auth.login(username, password)).access_token),
    loginAsGuest: async (name, email) => adopt((await auth.loginAsGuest(name, email)).access_token),
    logout: async () => {
      try { await auth.logout(); } finally { clear(); }
    },
  }), [status, principal, adopt, clear]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
