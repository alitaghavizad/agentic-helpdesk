import { apiFetch } from "../client";
import type { components } from "../schema";

export type Principal = components["schemas"]["PrincipalResponse"];
type TokenResponse = components["schemas"]["TokenResponse"];

export const login = (username: string, password: string) =>
  apiFetch<TokenResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });

export const loginAsGuest = (name: string, email: string) =>
  apiFetch<TokenResponse>("/api/auth/guest", {
    method: "POST",
    body: JSON.stringify({ name, email }),
  });

export const refresh = () => apiFetch<TokenResponse>("/api/auth/refresh", { method: "POST" });
export const logout = () => apiFetch<void>("/api/auth/logout", { method: "POST" });
export const me = () => apiFetch<Principal>("/api/auth/me");
