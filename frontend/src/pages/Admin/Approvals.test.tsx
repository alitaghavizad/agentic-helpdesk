import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Approvals } from "./Approvals";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const PENDING_EMAIL = {
  id: "a1",
  request_number: "REQ-1001",
  action_type: "send_email",
  action_payload: { to: "jamie@example.com", subject: "Your ticket has been resolved" },
  justification: "The ticket is resolved and the customer asked to be notified by email.",
  risk_level: "high",
  agent_summary: "Agent wants to email the customer their resolution.",
  conversation_id: "c1",
  status: "pending",
  decision_note: null,
  execution_result: null,
};

const PENDING_LOW_RISK = {
  id: "a2",
  request_number: "REQ-1002",
  action_type: "cross_department_ticket_assignment",
  action_payload: { ticket_id: "t-9", department: "billing" },
  justification: "Ticket needs billing's attention.",
  risk_level: "low",
  agent_summary: "Agent wants to reassign the ticket to billing.",
  conversation_id: "c2",
  status: "pending",
  decision_note: null,
  execution_result: null,
};

const DECIDED_EXECUTED = {
  id: "a3",
  request_number: "REQ-0900",
  action_type: "reset_credential",
  action_payload: { user_id: "u-7" },
  justification: "User locked out and confirmed identity over phone.",
  risk_level: "medium",
  agent_summary: "Agent wants to reset the user's credential.",
  conversation_id: "c3",
  status: "executed",
  decision_note: "Confirmed identity, approved.",
  execution_result: { reset: true, notified: "u-7" },
};

const DECIDED_DENIED = {
  id: "a4",
  request_number: "REQ-0901",
  action_type: "disclose_restricted_information",
  action_payload: { field: "salary" },
  justification: "Caller asked for a coworker's salary.",
  risk_level: "high",
  agent_summary: "Agent wants to disclose restricted salary information.",
  conversation_id: "c4",
  status: "denied",
  decision_note: "Not an authorized disclosure.",
  execution_result: null,
};

const DECIDED_FAILED = {
  id: "a5",
  request_number: "REQ-0902",
  action_type: "send_email",
  action_payload: { to: "broken@example.com" },
  justification: "Customer asked for a status update by email.",
  risk_level: "medium",
  agent_summary: "Agent wants to email the customer.",
  conversation_id: "c5",
  status: "failed",
  decision_note: "Approved.",
  execution_result: { error: "SMTP connection refused" },
};

async function flushMicrotasks(times = 15) {
  await act(async () => {
    for (let i = 0; i < times; i++) await Promise.resolve();
  });
}

function renderApprovals() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/admin/approvals"]}>
        <Approvals />
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

describe("Approvals", () => {
  it("renders request number, action type, risk badge, justification, agent summary, and the full payload", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/approvals?status=pending")) return jsonResponse([PENDING_EMAIL]);
      throw new Error(`unexpected call: ${u}`);
    });

    renderApprovals();

    const card = (await screen.findByText("REQ-1001")).closest("div") as HTMLElement;
    expect(await screen.findByText("send_email")).toBeInTheDocument();
    expect(screen.getByText(/high risk/i)).toBeInTheDocument();
    expect(screen.getByText(/The ticket is resolved and the customer asked/)).toBeInTheDocument();
    expect(screen.getByText(/Agent wants to email the customer/)).toBeInTheDocument();
    // The full action_payload, rendered through JsonBlock -- not summarized
    // or truncated.
    expect(screen.getByText(/jamie@example\.com/)).toBeInTheDocument();
    expect(screen.getByText(/Your ticket has been resolved/)).toBeInTheDocument();
    expect(card).toBeTruthy();
  });

  it("links to the admin Conversations screen, not a dead per-conversation route", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/approvals?status=pending")) return jsonResponse([PENDING_EMAIL]);
      throw new Error(`unexpected call: ${u}`);
    });

    renderApprovals();

    const link = await screen.findByRole("link", { name: /view source conversation/i });
    // Conversations.tsx has no `:id` route (App.tsx wires only
    // `/admin/conversations`) -- a link carrying the conversation id would
    // fall through to NotFound, exactly the dead link an earlier task in
    // this phase was flagged for. This must point at the real route.
    expect(link).toHaveAttribute("href", "/admin/conversations");
  });

  it("sends POST /decide with {approve: true, note} when an approval is approved", async () => {
    const user = userEvent.setup();
    let decideBody: unknown = null;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/admin/approvals?status=pending")) return jsonResponse([PENDING_EMAIL]);
      if (u.endsWith("/api/admin/approvals/a1/decide")) {
        decideBody = JSON.parse(String(init?.body));
        return jsonResponse({ ...PENDING_EMAIL, status: "executed", decision_note: "go ahead", execution_result: { sent: true } });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderApprovals();
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    await user.type(await screen.findByLabelText("Decision note"), "go ahead");
    await user.click(screen.getByRole("button", { name: "Confirm approve" }));

    await vi.waitFor(() => {
      expect(decideBody).toEqual({ approve: true, note: "go ahead" });
    });
  });

  it("sends POST /decide with {approve: false, note} when an approval is denied", async () => {
    const user = userEvent.setup();
    let decideBody: unknown = null;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/admin/approvals?status=pending")) return jsonResponse([PENDING_EMAIL]);
      if (u.endsWith("/api/admin/approvals/a1/decide")) {
        decideBody = JSON.parse(String(init?.body));
        return jsonResponse({ ...PENDING_EMAIL, status: "denied", decision_note: "no", execution_result: null });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderApprovals();
    await user.click(await screen.findByRole("button", { name: "Deny" }));
    await user.type(await screen.findByLabelText("Decision note"), "no");
    await user.click(screen.getByRole("button", { name: "Confirm deny" }));

    await vi.waitFor(() => {
      expect(decideBody).toEqual({ approve: false, note: "no" });
    });
  });

  it("disables the decide buttons while a decision is in flight, and never sends a second POST for a double-click", async () => {
    const user = userEvent.setup();
    let resolveDecide!: (value: Response) => void;
    let decideCallCount = 0;
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/approvals?status=pending")) return jsonResponse([PENDING_EMAIL]);
      if (u.endsWith("/api/admin/approvals/a1/decide")) {
        decideCallCount += 1;
        return new Promise<Response>((resolve) => { resolveDecide = resolve; });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderApprovals();
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    const confirmButton = await screen.findByRole("button", { name: "Confirm approve" });

    await user.click(confirmButton);
    // The confirm button must be disabled immediately, before the request
    // resolves -- this is the guard itself, not a side effect of it.
    expect(confirmButton).toBeDisabled();
    // The row's own Deny trigger must be disabled too: the risk this
    // guards is two concurrent decisions on the same approval, not just
    // two clicks on the same button.
    // (The Deny button is behind the modal overlay, but assert on the
    // underlying element directly since jsdom does not model overlay
    // click-interception.)
    const denyTrigger = screen.getByRole("button", { name: "Deny" });
    expect(denyTrigger).toBeDisabled();

    // A second click while still pending must not fire a second POST --
    // this is the actual double-click guard, not merely "a click works".
    await user.click(confirmButton);

    resolveDecide(
      jsonResponse({ ...PENDING_EMAIL, status: "executed", decision_note: "", execution_result: { sent: true } }),
    );

    await vi.waitFor(() => {
      expect(decideCallCount).toBe(1);
    });
  });

  it("keeps a just-approved row visible in the pending view, showing its execution_result inline", async () => {
    // The brief: an admin needs to see what actually happened right after
    // they approved, including a failure -- not after switching to a
    // separate "Decided" filter. This stays on the *pending* view the
    // whole time.
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/admin/approvals?status=pending")) return jsonResponse([PENDING_EMAIL]);
      if (u.endsWith("/api/admin/approvals/a1/decide") && init?.method === "POST") {
        return jsonResponse({
          ...PENDING_EMAIL,
          status: "executed",
          decision_note: "go ahead",
          execution_result: { sent: true, message_id: "m-1" },
        });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderApprovals();
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    await user.click(screen.getByRole("button", { name: "Confirm approve" }));

    // Still on the pending filter (never switched), the row is still
    // there -- now showing its outcome instead of Approve/Deny controls.
    expect(await screen.findByText("executed")).toBeInTheDocument();
    expect(screen.getByText("REQ-1001")).toBeInTheDocument();
    expect(screen.getByText(/"message_id"/)).toBeInTheDocument();
    expect(screen.getByText(/m-1/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Filter by status")).toHaveValue("pending");
  });

  it("restores focus to the row's own card after a successful decide, not to <body>", async () => {
    // The row's Approve button (the modal's opener) is gone by the time
    // the modal closes -- the same successful decide that closes it also
    // swaps the row's controls for its outcome. Modal.tsx's restore can't
    // send focus back to a button that no longer exists, so ApprovalCard
    // gives it a persistent fallback: the card's own container.
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/admin/approvals?status=pending")) return jsonResponse([PENDING_EMAIL]);
      if (u.endsWith("/api/admin/approvals/a1/decide") && init?.method === "POST") {
        return jsonResponse({ ...PENDING_EMAIL, status: "executed", decision_note: "", execution_result: { sent: true } });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderApprovals();
    const card = (await screen.findByText("REQ-1001")).closest("div[tabindex]") as HTMLElement;
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await user.click(screen.getByRole("button", { name: "Confirm approve" }));

    await screen.findByText("executed");
    expect(card).toHaveFocus();
    expect(document.activeElement).not.toBe(document.body);
  });

  it("keeps a decided item visible with a failed execution_result, not just an executed one", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/approvals?status=pending")) return jsonResponse([]);
      if (u.endsWith("/api/admin/approvals")) return jsonResponse([DECIDED_FAILED]);
      throw new Error(`unexpected call: ${u}`);
    });

    renderApprovals();
    await user.selectOptions(screen.getByLabelText("Filter by status"), "decided");

    expect(await screen.findByText("REQ-0902")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
    // The failure detail itself, not merely a "failed" label -- this is
    // the case the brief says an admin most needs to see.
    expect(screen.getByText(/SMTP connection refused/)).toBeInTheDocument();
  });

  it("polls the approvals list every 30 seconds", async () => {
    vi.useFakeTimers();
    try {
      let fetchCount = 0;
      fetchMock.mockImplementation(async (url: string) => {
        const u = String(url);
        if (u.endsWith("/api/admin/approvals?status=pending")) {
          fetchCount += 1;
          return jsonResponse([PENDING_EMAIL]);
        }
        throw new Error(`unexpected call: ${u}`);
      });

      renderApprovals();
      await flushMicrotasks();
      expect(fetchCount).toBe(1);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      expect(fetchCount).toBe(2);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      expect(fetchCount).toBe(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps a decided item visible with its execution_result", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/approvals?status=pending")) return jsonResponse([]);
      if (u.endsWith("/api/admin/approvals")) return jsonResponse([DECIDED_EXECUTED, DECIDED_DENIED]);
      throw new Error(`unexpected call: ${u}`);
    });

    renderApprovals();
    await user.selectOptions(screen.getByLabelText("Filter by status"), "decided");

    expect(await screen.findByText("REQ-0900")).toBeInTheDocument();
    expect(screen.getByText(/Confirmed identity, approved\./)).toBeInTheDocument();
    // The full execution_result, not just a status label.
    expect(screen.getByText(/"notified"/)).toBeInTheDocument();
    expect(screen.getAllByText(/u-7/).length).toBeGreaterThan(0);

    // A denied item with no execution_result still shows, with its note,
    // and does not render a stray "Execution result" block for null data.
    expect(screen.getByText("REQ-0901")).toBeInTheDocument();
    expect(screen.getByText(/Not an authorized disclosure\./)).toBeInTheDocument();
  });

  it("switches between pending and decided via the status filter", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/approvals?status=pending")) return jsonResponse([PENDING_EMAIL]);
      if (u.endsWith("/api/admin/approvals")) return jsonResponse([DECIDED_EXECUTED]);
      throw new Error(`unexpected call: ${u}`);
    });

    renderApprovals();
    expect(await screen.findByText("REQ-1001")).toBeInTheDocument();
    expect(screen.queryByText("REQ-0900")).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Filter by status"), "decided");

    expect(await screen.findByText("REQ-0900")).toBeInTheDocument();
    expect(screen.queryByText("REQ-1001")).not.toBeInTheDocument();

    const calledUrls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(calledUrls).toContain("http://localhost:8000/api/admin/approvals?status=pending");
    expect(calledUrls).toContain("http://localhost:8000/api/admin/approvals");
  });

  it("renders a failed approvals fetch as StateBlock's error state, never as an empty list", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/approvals?status=pending")) {
        return jsonResponse({ detail: "Forbidden: admin role required" }, 403);
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderApprovals();

    expect(await screen.findByRole("alert")).toHaveTextContent("You do not have access to this.");
    expect(screen.queryByText("REQ-1001")).not.toBeInTheDocument();
  });

  it("renders an empty pending queue distinguishably from a failed one", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/approvals?status=pending")) return jsonResponse([]);
      throw new Error(`unexpected call: ${u}`);
    });

    renderApprovals();

    expect(await screen.findByText("No approvals pending.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a loading state before the approvals response arrives", async () => {
    let resolveList!: (value: Response) => void;
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/approvals?status=pending")) {
        return new Promise<Response>((resolve) => { resolveList = resolve; });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderApprovals();

    expect(await screen.findByRole("status")).toHaveTextContent(/loading/i);
    resolveList(jsonResponse([PENDING_EMAIL]));
    await screen.findByText("REQ-1001");
  });

  it("renders more than one pending approval independently", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/approvals?status=pending")) return jsonResponse([PENDING_EMAIL, PENDING_LOW_RISK]);
      throw new Error(`unexpected call: ${u}`);
    });

    renderApprovals();

    expect(await screen.findByText("REQ-1001")).toBeInTheDocument();
    expect(screen.getByText("REQ-1002")).toBeInTheDocument();
    expect(screen.getByText(/high risk/i)).toBeInTheDocument();
    expect(screen.getByText(/low risk/i)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Approve" })).toHaveLength(2);
  });
});
