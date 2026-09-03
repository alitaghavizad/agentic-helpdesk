import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Audit } from "./Audit";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const ENTRY_USER_UPDATE = {
  id: "a1",
  actor_type: "user",
  actor_id: "admin-1",
  action: "user.updated",
  target_type: "user",
  target_id: "u-42",
  payload: { previous_role: "employee", new_role: "helpdesk" },
  ip_address: "10.0.0.5",
  created_at: "2026-09-01T10:00:00Z",
};

const ENTRY_SYSTEM = {
  id: "a2",
  actor_type: "system",
  actor_id: null,
  action: "lesson.archived",
  target_type: "lesson",
  target_id: "l-9",
  payload: {},
  ip_address: null,
  created_at: "2026-08-30T08:00:00Z",
};

function auditPage(items: unknown[], { limit = 50, offset = 0, total = items.length } = {}) {
  return { items, limit, offset, total };
}

/** Every request this suite's mock sees, as parsed `{path, params}` --
 * lets assertions check individual query parameters (actor_id, action,
 * etc.) without depending on the order URLSearchParams happens to
 * serialise them in. */
function callsToAdminAudit(): URLSearchParams[] {
  return fetchMock.mock.calls
    .map((call: unknown[]) => String(call[0]))
    .filter((u: string) => u.includes("/api/admin/audit"))
    .map((u: string) => new URL(u).searchParams);
}

function renderAudit() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/admin/audit"]}>
        <Audit />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Admin Audit", () => {
  it("renders actor, action, target, payload and timestamp", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.startsWith("http://localhost:8000/api/admin/audit")) return jsonResponse(auditPage([ENTRY_USER_UPDATE]));
      throw new Error(`unexpected call: ${u}`);
    });

    renderAudit();

    expect(await screen.findByText(/user.*admin-1/)).toBeInTheDocument();
    expect(screen.getByText("user.updated")).toBeInTheDocument();
    expect(screen.getByText(/user.*u-42/)).toBeInTheDocument();
    // The full payload, through JsonBlock -- not summarised.
    expect(screen.getByText(/"previous_role"/)).toBeInTheDocument();
    expect(screen.getByText(/"helpdesk"/)).toBeInTheDocument();
    expect(screen.getByText(new Date(ENTRY_USER_UPDATE.created_at).toLocaleString())).toBeInTheDocument();
  });

  it("renders a null actor_id and a null ip_address without crashing", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.startsWith("http://localhost:8000/api/admin/audit")) return jsonResponse(auditPage([ENTRY_SYSTEM]));
      throw new Error(`unexpected call: ${u}`);
    });

    renderAudit();

    // actor_id is null for a SYSTEM-actor row -- falls back to just the
    // actor_type, not "system · null" or a crash.
    expect(await screen.findByText("system")).toBeInTheDocument();
    expect(screen.getByText("lesson.archived")).toBeInTheDocument();
    // ip_address null renders as an explicit placeholder.
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("re-queries with actor_id when the actor filter changes", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.startsWith("http://localhost:8000/api/admin/audit")) return jsonResponse(auditPage([ENTRY_USER_UPDATE]));
      throw new Error(`unexpected call: ${u}`);
    });

    renderAudit();
    await screen.findByText("user.updated");
    await user.type(screen.getByLabelText("Filter by actor"), "admin-1");

    await vi.waitFor(() => {
      const calls = callsToAdminAudit();
      expect(calls.some((params) => params.get("actor_id") === "admin-1")).toBe(true);
    });
  });

  it("re-queries with action when the action filter changes", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.startsWith("http://localhost:8000/api/admin/audit")) return jsonResponse(auditPage([ENTRY_USER_UPDATE]));
      throw new Error(`unexpected call: ${u}`);
    });

    renderAudit();
    await screen.findByText("user.updated");
    await user.type(screen.getByLabelText("Filter by action"), "lesson.archived");

    await vi.waitFor(() => {
      const calls = callsToAdminAudit();
      expect(calls.some((params) => params.get("action") === "lesson.archived")).toBe(true);
    });
  });

  it("re-queries with target_type when the target filter changes", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.startsWith("http://localhost:8000/api/admin/audit")) return jsonResponse(auditPage([ENTRY_USER_UPDATE]));
      throw new Error(`unexpected call: ${u}`);
    });

    renderAudit();
    await screen.findByText("user.updated");
    await user.type(screen.getByLabelText("Filter by target type"), "lesson");

    await vi.waitFor(() => {
      const calls = callsToAdminAudit();
      expect(calls.some((params) => params.get("target_type") === "lesson")).toBe(true);
    });
  });

  it("re-queries with an ISO since bound when the since-date filter changes", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.startsWith("http://localhost:8000/api/admin/audit")) return jsonResponse(auditPage([ENTRY_USER_UPDATE]));
      throw new Error(`unexpected call: ${u}`);
    });

    renderAudit();
    await screen.findByText("user.updated");
    fireEvent.change(screen.getByLabelText("Filter by since date"), { target: { value: "2026-08-01" } });

    await vi.waitFor(() => {
      const calls = callsToAdminAudit();
      expect(calls.some((params) => params.get("since") === new Date("2026-08-01").toISOString())).toBe(true);
    });
  });

  it("resets to the first page when a filter changes", async () => {
    const user = userEvent.setup();
    const page2 = [{ ...ENTRY_USER_UPDATE, id: "a-page2" }];
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      const params = new URL(u).searchParams;
      if (!u.startsWith("http://localhost:8000/api/admin/audit")) throw new Error(`unexpected call: ${u}`);
      if (params.get("offset") === "50" && !params.get("action")) {
        return jsonResponse(auditPage(page2, { offset: 50, total: 60 }));
      }
      if (params.get("action") === "lesson.archived") {
        return jsonResponse(auditPage([ENTRY_SYSTEM], { offset: 0, total: 1 }));
      }
      return jsonResponse(auditPage(Array.from({ length: 50 }, (_, i) => ({ ...ENTRY_USER_UPDATE, id: `p1-${i}` })), { offset: 0, total: 60 }));
    });

    renderAudit();
    await screen.findByText("Showing 1–50 of 60");
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByText("Showing 51–60 of 60");

    await user.type(screen.getByLabelText("Filter by action"), "lesson.archived");

    // Back on the first page's worth of results for the new filter, not
    // still sitting at offset=50 (which would show nothing at all for a
    // narrower, one-row result and read as an empty log).
    await vi.waitFor(() => {
      expect(screen.getByText("lesson.archived")).toBeInTheDocument();
    });
    expect(screen.queryByText("Showing 51–60 of 60")).not.toBeInTheDocument();
  });

  it("renders 'No matching entries.' for an empty filtered result, not a blank table", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.startsWith("http://localhost:8000/api/admin/audit")) return jsonResponse(auditPage([], { total: 0 }));
      throw new Error(`unexpected call: ${u}`);
    });

    renderAudit();
    expect(await screen.findByText("No matching entries.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a loading state before the audit response arrives", async () => {
    let resolveList!: (value: Response) => void;
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.startsWith("http://localhost:8000/api/admin/audit")) {
        return new Promise<Response>((resolve) => {
          resolveList = resolve;
        });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderAudit();
    expect(await screen.findByRole("status")).toHaveTextContent(/loading/i);
    resolveList(jsonResponse(auditPage([ENTRY_USER_UPDATE])));
    await screen.findByText("user.updated");
  });

  it("renders a failed audit fetch as StateBlock's error state, never as an empty table", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.startsWith("http://localhost:8000/api/admin/audit")) return jsonResponse({ detail: "Forbidden: admin role required" }, 403);
      throw new Error(`unexpected call: ${u}`);
    });

    renderAudit();
    expect(await screen.findByRole("alert")).toHaveTextContent("You do not have access to this.");
    expect(screen.queryByText("user.updated")).not.toBeInTheDocument();
  });
});
