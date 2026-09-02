import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, setAccessToken, setAuthFailureHandler } from "./client";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  setAccessToken("token-1");
  setAuthFailureHandler(() => {});
});

afterEach(() => vi.unstubAllGlobals());

describe("apiFetch", () => {
  it("sends the bearer token and credentials", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await apiFetch("/api/health");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/api/health");
    expect(new Headers(init.headers).get("authorization")).toBe("Bearer token-1");
    expect(init.credentials).toBe("include");
  });

  it("refreshes once on 401 and retries with the new token", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401))
      .mockResolvedValueOnce(jsonResponse({ access_token: "token-2" }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    await expect(apiFetch<{ ok: boolean }>("/api/tickets")).resolves.toEqual({ ok: true });

    const retry = fetchMock.mock.calls[2][1];
    expect(new Headers(retry.headers).get("authorization")).toBe("Bearer token-2");
  });

  it("refreshes ONCE for concurrent 401s, not once per request", async () => {
    // The load-bearing assertion of this module. Four screens mount at the
    // same moment on an expired token; without a shared in-flight promise
    // each fires its own refresh, and every refresh after the first
    // presents a token the server has already rotated and revoked -- so
    // three of the four log the user out.
    let refreshes = 0;
    fetchMock.mockImplementation(async (url: string, init: RequestInit) => {
      if (url.endsWith("/api/auth/refresh")) {
        refreshes += 1;
        return jsonResponse({ access_token: "token-2" });
      }
      return new Headers(init.headers).get("authorization") === "Bearer token-2"
        ? jsonResponse({ ok: true })
        : jsonResponse({ detail: "expired" }, 401);
    });

    const results = await Promise.all([
      apiFetch("/api/a"), apiFetch("/api/b"), apiFetch("/api/c"), apiFetch("/api/d"),
    ]);

    expect(refreshes).toBe(1);
    expect(results).toEqual([{ ok: true }, { ok: true }, { ok: true }, { ok: true }]);
  });

  it("calls the auth-failure handler when the refresh itself fails", async () => {
    const onFailure = vi.fn();
    setAuthFailureHandler(onFailure);
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401))
      .mockResolvedValueOnce(jsonResponse({ detail: "no cookie" }, 401));

    await expect(apiFetch("/api/tickets")).rejects.toBeInstanceOf(ApiError);
    expect(onFailure).toHaveBeenCalledOnce();
  });

  it("does not try to refresh a failed refresh call itself", async () => {
    // Otherwise a 401 from /auth/refresh recurses until the stack blows.
    fetchMock.mockResolvedValue(jsonResponse({ detail: "no cookie" }, 401));
    await expect(apiFetch("/api/auth/refresh", { method: "POST" })).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("throws ApiError carrying FastAPI's detail", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "no such ticket" }, 404));
    await expect(apiFetch("/api/tickets/x")).rejects.toMatchObject({
      status: 404,
      detail: "no such ticket",
    });
  });

  it("returns undefined for a 204 rather than choking on an empty body", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(apiFetch("/api/auth/logout", { method: "POST" })).resolves.toBeUndefined();
  });
});
