import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NotificationBell } from "./NotificationBell";
import * as authCtx from "../auth/AuthContext";

// fetch is stubbed directly, the same way useNotifications.test.tsx does it
// (useNotifications is this component's only data source) -- MSW is not
// used anywhere in this project.
const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

/** A `text/event-stream` response that never closes on its own, so the hook
 * never runs its reconnect path during these tests. */
function openStream() {
  return new Response(new ReadableStream(), { headers: { "content-type": "text/event-stream" } });
}

const USER = {
  kind: "user", user_id: "u1", role: "employee", clearance: "standard",
  department: "Engineering", employee_ref: "EMP-1", helpdesk_ref: null,
  username: "j.doe", full_name: "Jane Doe",
};

function renderBell(backlog: unknown[]) {
  vi.spyOn(authCtx, "useAuth").mockReturnValue({
    status: "signed-in", principal: USER, login: vi.fn(), loginAsGuest: vi.fn(), logout: vi.fn(),
  } as never);
  fetchMock.mockImplementation(async (url: string) => {
    if (String(url).includes("/stream")) return openStream();
    return jsonResponse(backlog);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <NotificationBell />
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

describe("NotificationBell", () => {
  it("deep-links a ticket notification to its ticket, not the list", async () => {
    // Task 5 adds the /tickets/:id route this now targets -- before that
    // route existed, pathFor() returned null unconditionally for every
    // link_type, which is exactly what this test would catch a regression
    // back to.
    renderBell([
      {
        id: "n1", type: "ticket_updated", title: "Ticket updated", body: "Your ticket moved",
        link_type: "ticket", link_id: "tkt-42", read: false, created_at: "2026-09-02T10:00:00Z",
      },
    ]);

    await userEvent.click(screen.getByRole("button", { name: /notifications/i }));

    const link = await screen.findByRole("link", { name: /ticket updated/i });
    expect(link).toHaveAttribute("href", "/tickets/tkt-42");
  });

  it("renders a notification with no route-backed link_type as plain text, not a link", async () => {
    renderBell([
      {
        id: "n2", type: "approval_requested", title: "Approval needed", body: "Please review",
        link_type: "approval", link_id: "apr-1", read: false, created_at: "2026-09-02T10:00:00Z",
      },
    ]);

    await userEvent.click(screen.getByRole("button", { name: /notifications/i }));

    await screen.findByText("Approval needed");
    expect(screen.queryByRole("link", { name: /approval needed/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approval needed/i })).toBeInTheDocument();
  });
});
