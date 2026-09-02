/**
 * The only module that performs HTTP. Deliberately React-free: the auth
 * context pushes the token in through setAccessToken, so this can be tested
 * without rendering anything.
 */
const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
const REFRESH_PATH = "/api/auth/refresh";

export class ApiError extends Error {
  constructor(readonly status: number, readonly detail: string) {
    super(`${status}: ${detail}`);
    this.name = "ApiError";
  }
}

let accessToken: string | null = null;
let onAuthFailure: () => void = () => {};

export function setAccessToken(token: string | null): void {
  accessToken = token;
}
export function getAccessToken(): string | null {
  return accessToken;
}
export function setAuthFailureHandler(handler: () => void): void {
  onAuthFailure = handler;
}

/**
 * One shared in-flight refresh, not one per caller.
 *
 * Several screens mount at once and all 401 together on an expired token.
 * Refresh tokens are single-use and rotated server-side (app/auth/router.py
 * revokes the presented one), so a second concurrent refresh presents a
 * token that was just revoked and fails -- logging the user out in the
 * middle of a successful recovery. Every 401 therefore awaits the same
 * promise.
 */
let refreshInFlight: Promise<string | null> | null = null;

function refreshOnce(): Promise<string | null> {
  if (!refreshInFlight) {
    refreshInFlight = fetch(`${BASE}${REFRESH_PATH}`, {
      method: "POST",
      credentials: "include",
    })
      .then(async (response) => {
        if (!response.ok) return null;
        const body = (await response.json()) as { access_token: string };
        accessToken = body.access_token;
        return body.access_token;
      })
      .catch(() => null)
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

function buildInit(init: RequestInit, token: string | null): RequestInit {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body !== undefined && !(init.body instanceof FormData) && !headers.has("content-type")) {
    headers.set("Content-Type", "application/json");
  }
  return { ...init, headers, credentials: "include" };
}

async function raise(response: Response): Promise<never> {
  let detail = response.statusText;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
    else if (body.detail !== undefined) detail = JSON.stringify(body.detail);
  } catch {
    // A non-JSON error body (a proxy's HTML, an empty 502). statusText stands.
  }
  throw new ApiError(response.status, detail);
}

/** Performs the request, transparently refreshing once on a 401. */
async function send(path: string, init: RequestInit): Promise<Response> {
  const first = await fetch(`${BASE}${path}`, buildInit(init, accessToken));
  // Refreshing a failed refresh would recurse forever.
  if (first.status !== 401 || path === REFRESH_PATH) return first;

  const token = await refreshOnce();
  if (!token) {
    onAuthFailure();
    return first;
  }
  return fetch(`${BASE}${path}`, buildInit(init, token));
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await send(path, init);
  if (!response.ok) await raise(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * Same auth and refresh behaviour, but hands back the Response so the caller
 * can read a stream body. Used only by the three SSE consumers.
 */
export async function apiStream(path: string, init: RequestInit = {}): Promise<Response> {
  // Built through a Headers object rather than object-spreading init.headers:
  // a caller-supplied Headers instance has no enumerable own properties, so
  // `{ Accept: ..., ...init.headers }` silently drops every header on it.
  const headers = new Headers(init.headers);
  if (!headers.has("Accept")) headers.set("Accept", "text/event-stream");
  const response = await send(path, { ...init, headers });
  if (!response.ok) await raise(response);
  return response;
}
