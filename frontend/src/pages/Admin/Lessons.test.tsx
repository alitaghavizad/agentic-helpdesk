import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Lessons } from "./Lessons";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const LESSON_ACTIVE = {
  id: "l1",
  title: "Reset password via SSO",
  category: "account_access",
  content_md: "Always verify identity before resetting a password.",
  status: "active",
  confidence: "high",
  ticket_id: "t-501",
  created_at: "2026-08-30T12:00:00Z",
};

const LESSON_NO_TICKET = {
  id: "l2",
  title: "General escalation policy",
  category: "process",
  content_md: "Escalate after two failed attempts.",
  status: "active",
  confidence: "medium",
  ticket_id: null,
  created_at: "2026-08-29T09:00:00Z",
};

function lessonsPage(items: unknown[], { limit = 50, offset = 0, total = items.length } = {}) {
  return { items, limit, offset, total };
}

function renderLessons() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/admin/lessons"]}>
        <Lessons />
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

describe("Admin Lessons", () => {
  it("renders title, category, status, confidence and source ticket", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/lessons?offset=0")) return jsonResponse(lessonsPage([LESSON_ACTIVE, LESSON_NO_TICKET]));
      throw new Error(`unexpected call: ${u}`);
    });

    renderLessons();

    expect(await screen.findByText("Reset password via SSO")).toBeInTheDocument();
    expect(screen.getByText("account_access")).toBeInTheDocument();
    expect(screen.getAllByText("active")).toHaveLength(2);
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText("t-501")).toBeInTheDocument();

    // A lesson with no ticket_id (not produced from an incident) shows a
    // placeholder rather than a blank cell or "null".
    expect(screen.getByText("General escalation policy")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(1);
  });

  it("sends PATCH {content_md} when a lesson's content is edited, and re-renders the row from the full LessonSummary response", async () => {
    const user = userEvent.setup();
    let patchBody: unknown = null;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/admin/lessons?offset=0")) return jsonResponse(lessonsPage([LESSON_ACTIVE]));
      if (u.endsWith("/api/admin/lessons/l1") && init?.method === "PATCH") {
        patchBody = JSON.parse(String(init.body));
        // The response is the FULL LessonSummary (Task 0 widened this
        // specifically so the row re-renders from it) -- it deliberately
        // differs from a naive local echo (title included, even though
        // this edit only touched content) to prove the row comes from
        // here, not from a client-side merge of the textarea value alone.
        return jsonResponse({
          ...LESSON_ACTIVE,
          title: "Reset password via SSO (revised)",
          content_md: "Always verify identity via two factors before resetting a password.",
        });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderLessons();
    await user.click(await screen.findByRole("button", { name: "Edit" }));

    const textarea = await screen.findByLabelText("Lesson content");
    await user.clear(textarea);
    await user.type(textarea, "Always verify identity via two factors before resetting a password.");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await vi.waitFor(() => {
      expect(patchBody).toEqual({
        content_md: "Always verify identity via two factors before resetting a password.",
      });
    });

    // The modal closes and the table shows the server's own title, proving
    // the re-render came from the PATCH response.
    expect(await screen.findByText("Reset password via SSO (revised)")).toBeInTheDocument();
    expect(screen.queryByLabelText("Lesson content")).not.toBeInTheDocument();
  });

  it("archives a lesson: sends DELETE, and the row stays visible with an 'archived' badge instead of vanishing", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/admin/lessons?offset=0")) return jsonResponse(lessonsPage([LESSON_ACTIVE]));
      if (u.endsWith("/api/admin/lessons/l1") && init?.method === "DELETE") {
        return jsonResponse({ id: "l1", status: "archived", archived: true });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderLessons();
    expect(await screen.findByText("Reset password via SSO")).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Archive" }));

    // The backend archives, it does not delete -- the row and its title
    // must still be on screen, now flagged archived rather than gone.
    await vi.waitFor(() => {
      expect(screen.getByText("archived")).toBeInTheDocument();
    });
    expect(screen.getByText("Reset password via SSO")).toBeInTheDocument();
    expect(screen.queryByText("active")).not.toBeInTheDocument();
  });

  it("archiving an already-archived lesson twice is not an error: two DELETE calls, no alert, row still shown", async () => {
    const user = userEvent.setup();
    let deleteCalls = 0;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/admin/lessons?offset=0")) return jsonResponse(lessonsPage([LESSON_ACTIVE]));
      if (u.endsWith("/api/admin/lessons/l1") && init?.method === "DELETE") {
        deleteCalls += 1;
        // Idempotent per the backend's own contract: 200 with the same
        // body both times, never a 409 on the second call.
        return jsonResponse({ id: "l1", status: "archived", archived: true });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderLessons();
    const archiveButton = await screen.findByRole("button", { name: "Archive" });
    await user.click(archiveButton);
    await vi.waitFor(() => expect(screen.getByText("archived")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Archive" }));
    await vi.waitFor(() => expect(deleteCalls).toBe(2));

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("Reset password via SSO")).toBeInTheDocument();
    expect(screen.getByText("archived")).toBeInTheDocument();
  });

  it("pages using the response's limit and offset", async () => {
    const user = userEvent.setup();
    const page1 = Array.from({ length: 2 }, (_, i) => ({ ...LESSON_ACTIVE, id: `p1-${i}`, title: `Lesson ${i}` }));
    const page2 = [{ ...LESSON_ACTIVE, id: "p2-0", title: "Lesson 2" }];
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/lessons?offset=0")) return jsonResponse(lessonsPage(page1, { limit: 2, total: 3 }));
      if (u.endsWith("/api/admin/lessons?offset=2")) return jsonResponse(lessonsPage(page2, { limit: 2, offset: 2, total: 3 }));
      throw new Error(`unexpected call: ${u}`);
    });

    renderLessons();
    expect(await screen.findByText("Lesson 0")).toBeInTheDocument();
    expect(screen.getByText("Showing 1–2 of 3")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("Lesson 2")).toBeInTheDocument();
    expect(screen.queryByText("Lesson 0")).not.toBeInTheDocument();
  });

  it("issues exactly one request for a page, not one per lesson", async () => {
    const items = Array.from({ length: 5 }, (_, i) => ({ ...LESSON_ACTIVE, id: `r-${i}`, title: `Lesson ${i}` }));
    let calls = 0;
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/lessons?offset=0")) {
        calls += 1;
        return jsonResponse(lessonsPage(items, { total: 5 }));
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderLessons();
    expect(await screen.findByText("Lesson 0")).toBeInTheDocument();
    expect(screen.getByText("Lesson 4")).toBeInTheDocument();

    await act(async () => {
      for (let i = 0; i < 10; i++) await Promise.resolve();
    });
    expect(calls).toBe(1);
  });

  it("shows a loading state before the lessons response arrives", async () => {
    let resolveList!: (value: Response) => void;
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/lessons?offset=0")) {
        return new Promise<Response>((resolve) => {
          resolveList = resolve;
        });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderLessons();
    expect(await screen.findByRole("status")).toHaveTextContent(/loading/i);
    resolveList(jsonResponse(lessonsPage([LESSON_ACTIVE])));
    await screen.findByText("Reset password via SSO");
  });

  it("renders a failed lessons fetch as StateBlock's error state, never as an empty table", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/lessons?offset=0")) return jsonResponse({ detail: "Forbidden: admin role required" }, 403);
      throw new Error(`unexpected call: ${u}`);
    });

    renderLessons();
    expect(await screen.findByRole("alert")).toHaveTextContent("You do not have access to this.");
    expect(screen.queryByText("Reset password via SSO")).not.toBeInTheDocument();
  });

  it("renders an empty lessons list distinguishably from a failed one", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/lessons?offset=0")) return jsonResponse(lessonsPage([], { total: 0 }));
      throw new Error(`unexpected call: ${u}`);
    });

    renderLessons();
    expect(await screen.findByText("No lessons recorded yet.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
