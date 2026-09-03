import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as admin from "./admin";

// fetch is stubbed directly, the same way every other endpoints/test in
// this project does it -- MSW is not used anywhere here. This file exists
// because admin.ts is consumed by every remaining phase-8b task (7-11):
// pinning the exact path/method/body each export sends now is what keeps a
// later refactor from silently breaking a task that has not been written
// yet.
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
  fetchMock.mockResolvedValue(jsonResponse({}));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function calledUrl(): string {
  return String(fetchMock.mock.calls[0][0]);
}
function calledInit(): RequestInit {
  return fetchMock.mock.calls[0][1] as RequestInit;
}

describe("admin endpoints", () => {
  it("adminOverview: GET /api/admin/overview", async () => {
    await admin.adminOverview();
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/overview");
  });

  it("adminCosts: GET /api/admin/costs", async () => {
    await admin.adminCosts();
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/costs");
  });

  it("adminRuns: GET /api/admin/runs with no query when no params given", async () => {
    await admin.adminRuns();
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/runs");
  });

  it("adminRuns: forwards limit and offset as query params", async () => {
    await admin.adminRuns({ limit: 25, offset: 50 });
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/runs?limit=25&offset=50");
  });

  it("adminTrace: GET /api/admin/runs/{run_id}/trace", async () => {
    await admin.adminTrace("run-1");
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/runs/run-1/trace");
  });

  it("runsStream: opens GET /api/admin/runs/stream via apiStream, not apiFetch", async () => {
    fetchMock.mockResolvedValueOnce(new Response(new ReadableStream(), {
      headers: { "content-type": "text/event-stream" },
    }));
    const response = await admin.runsStream({});
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/runs/stream");
    // apiStream sets Accept: text/event-stream and hands back the raw
    // Response rather than parsing a body -- if this had gone through
    // apiFetch instead, response.json() below would be the thing under
    // test, not a Response with a body stream.
    expect(new Headers(calledInit().headers).get("accept")).toBe("text/event-stream");
    expect(response.body).toBeInstanceOf(ReadableStream);
  });

  it("adminConversations: GET /api/admin/conversations with no query when no params given", async () => {
    await admin.adminConversations();
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/conversations");
  });

  it("adminConversations: forwards q, limit and offset as query params", async () => {
    await admin.adminConversations({ q: "vpn", limit: 10, offset: 20 });
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/conversations?q=vpn&limit=10&offset=20");
  });

  it("adminConversationDetail: GET /api/admin/conversations/{id}", async () => {
    await admin.adminConversationDetail("c1");
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/conversations/c1");
  });

  it("adminAudit: forwards every filter as a query param, dropping unset ones", async () => {
    await admin.adminAudit({ action: "user.patched", limit: 5 });
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/audit?action=user.patched&limit=5");
  });

  it("adminAudit: sends no query string at all when called with no filters", async () => {
    await admin.adminAudit();
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/audit");
  });

  it("adminUsers: GET /api/admin/users", async () => {
    await admin.adminUsers({ limit: 100 });
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/users?limit=100");
  });

  it("patchUser: PATCH /api/admin/users/{id} with the role/clearance body", async () => {
    await admin.patchUser("u1", { role: "helpdesk" });
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/users/u1");
    expect(calledInit().method).toBe("PATCH");
    expect(JSON.parse(calledInit().body as string)).toEqual({ role: "helpdesk" });
  });

  it("adminLessons: GET /api/admin/lessons", async () => {
    await admin.adminLessons();
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/lessons");
  });

  it("patchLesson: PATCH /api/admin/lessons/{id} with the patch body", async () => {
    await admin.patchLesson("l1", { status: "archived" });
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/lessons/l1");
    expect(calledInit().method).toBe("PATCH");
    expect(JSON.parse(calledInit().body as string)).toEqual({ status: "archived" });
  });

  it("archiveLesson: DELETE /api/admin/lessons/{id}", async () => {
    await admin.archiveLesson("l1");
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/lessons/l1");
    expect(calledInit().method).toBe("DELETE");
  });

  it("adminApprovals: GET /api/admin/approvals with no query when status is omitted", async () => {
    await admin.adminApprovals();
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/approvals");
  });

  it("adminApprovals: forwards a given status as a query param", async () => {
    await admin.adminApprovals("pending");
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/approvals?status=pending");
  });

  it("decideApproval: POST /api/admin/approvals/{id}/decide with the decision body", async () => {
    await admin.decideApproval("req-1", { approve: true, note: "looks fine" });
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/approvals/req-1/decide");
    expect(calledInit().method).toBe("POST");
    expect(JSON.parse(calledInit().body as string)).toEqual({ approve: true, note: "looks fine" });
  });

  it("buildDossier: POST /api/admin/tickets/{ticket_id}/dossier with no body", async () => {
    await admin.buildDossier("t1");
    expect(calledUrl()).toBe("http://localhost:8000/api/admin/tickets/t1/dossier");
    expect(calledInit().method).toBe("POST");
    expect(calledInit().body).toBeUndefined();
  });
});
