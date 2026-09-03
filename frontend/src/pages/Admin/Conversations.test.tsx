import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Conversations } from "./Conversations";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const CONVO_USER = {
  id: "c1",
  title: "Printer will not connect",
  status: "open",
  created_at: "2026-09-01T10:00:00Z",
  user_id: "u-42",
  username: "ada",
  full_name: "Ada Lovelace",
  guest_name: null,
  guest_email: null,
};

const CONVO_GUEST = {
  id: "c2",
  title: "Cannot reset password",
  status: "closed",
  created_at: "2026-09-01T09:00:00Z",
  user_id: null,
  username: null,
  full_name: null,
  guest_name: "Jamie Rivera",
  guest_email: "jamie@example.com",
};

// A user_id conversation whose `users` row could not be joined (e.g. a
// race with account deletion) -- username/full_name both null, but
// user_id is set. The id is the last-resort fallback for exactly this
// case, not the common case.
const CONVO_USER_UNJOINABLE = {
  id: "c3",
  title: "Cannot print",
  status: "open",
  created_at: "2026-09-01T11:00:00Z",
  user_id: "u-99",
  username: null,
  full_name: null,
  guest_name: null,
  guest_email: null,
};

const CONVERSATIONS_PAGE = { items: [CONVO_USER, CONVO_GUEST], limit: 50, offset: 0, total: 2 };

const RUN_1 = {
  id: "r1",
  trigger: "chat_turn",
  status: "ok",
  started_at: "2026-09-01T10:00:05Z",
  duration_ms: 1500,
  cost_usd: 0.02,
  llm_calls: 2,
  tool_calls: 0,
  error: null,
};

const RUN_2 = {
  id: "r2",
  trigger: "chat_turn",
  status: "error",
  started_at: "2026-09-01T10:05:00Z",
  duration_ms: 300,
  cost_usd: null,
  llm_calls: 1,
  tool_calls: 0,
  error: "Model refused",
};

const MESSAGE_TEXT = {
  id: "m1",
  role: "user",
  created_at: "2026-09-01T10:00:00Z",
  run_id: null,
  content: [{ type: "text", text: "My printer will not connect to the network." }],
};

const MESSAGE_ASSISTANT_MIXED = {
  id: "m2",
  role: "assistant",
  created_at: "2026-09-01T10:00:06Z",
  run_id: "r1",
  content: [
    { type: "text", text: "Let me check that for you." },
    { type: "image", source: { type: "base64", media_type: "image/png", data: "iVBOR..." } },
    { type: "tool_result", tool_use_id: "tu_1", content: [{ type: "text", text: "diagnostic output" }] },
    { type: "some_future_block_kind", payload: { nested: true } },
  ],
};

const CONVO_USER_DETAIL_TWO_RUNS = {
  conversation: CONVO_USER,
  messages: [MESSAGE_TEXT, MESSAGE_ASSISTANT_MIXED],
  runs: [RUN_2, RUN_1],
};

const CONVO_GUEST_DETAIL_NO_RUNS = {
  conversation: CONVO_GUEST,
  messages: [MESSAGE_TEXT],
  runs: [],
};

const TRACE_R1 = {
  run: {
    id: "r1",
    trigger: "chat_turn",
    status: "ok",
    started_at: "2026-09-01T10:00:05Z",
    duration_ms: 1500,
    cost_usd: 0.02,
    input_tokens: 100,
    output_tokens: 50,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    error: null,
  },
  roots: [
    {
      id: "span-1",
      kind: "llm",
      name: "answer",
      status: "ok",
      error: null,
      model: "claude-opus-5",
      duration_ms: 1500,
      input_tokens: 100,
      output_tokens: 50,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      cost_usd: 0.02,
      input: { prompt: "hello" },
      output: { text: "hi" },
      children: [],
    },
  ],
  span_count: 1,
  truncated: false,
};

const TRACE_R1_TRUNCATED = { ...TRACE_R1, span_count: 500, truncated: true };

function renderConversations() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/admin/conversations"]}>
        <Conversations />
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

describe("Conversations", () => {
  it("renders each conversation's title, participant, status, and created date", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/conversations?limit=50")) return jsonResponse(CONVERSATIONS_PAGE);
      throw new Error(`unexpected call: ${u}`);
    });

    renderConversations();

    const userRow = (await screen.findByText("Printer will not connect")).closest("tr") as HTMLElement;
    expect(within(userRow).getByText("Ada Lovelace")).toBeInTheDocument();
    expect(within(userRow).getByText("open")).toBeInTheDocument();
    expect(within(userRow).getByText(new Date("2026-09-01T10:00:00Z").toLocaleString())).toBeInTheDocument();

    const guestRow = screen.getByText("Cannot reset password").closest("tr") as HTMLElement;
    expect(within(guestRow).getByText("Jamie Rivera <jamie@example.com>")).toBeInTheDocument();
    expect(within(guestRow).getByText("closed")).toBeInTheDocument();
  });

  it("renders the participant's full_name, falling back to username, and to the id only as a last resort", async () => {
    // Reviewer finding: search already matches on username/full_name
    // server-side (backend/app/admin/queries.py's outer join), but the
    // response used to carry only user_id -- an admin who searched
    // "jamie" and got a row back could not see that "jamie" was what
    // matched. This pins the full fallback chain, including the case
    // where neither name is available (an unjoinable user_id).
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/conversations?limit=50")) {
        return jsonResponse({
          items: [
            CONVO_USER,
            { ...CONVO_USER, id: "c1b", title: "Username only", full_name: null, username: "ada" },
            CONVO_USER_UNJOINABLE,
          ],
          limit: 50, offset: 0, total: 3,
        });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderConversations();

    expect(await screen.findByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByText("ada")).toBeInTheDocument();
    expect(screen.getByText("User u-99")).toBeInTheDocument();
  });

  it("re-queries with ?q= when the search box is used", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.includes("/api/admin/conversations")) return jsonResponse(CONVERSATIONS_PAGE);
      throw new Error(`unexpected call: ${u}`);
    });

    renderConversations();
    await screen.findByText("Printer will not connect");

    await user.type(screen.getByLabelText("Search conversations"), "jamie");

    await vi.waitFor(() => {
      const calledWithQ = fetchMock.mock.calls.some(([url]) => String(url).includes("q=jamie"));
      expect(calledWithQ).toBe(true);
    });
  });

  it("clears the selected conversation's detail panel when the search box changes", async () => {
    // A new search term can filter the previously-selected row right out
    // of the list -- leaving the detail panel open would show a transcript
    // for a conversation the admin can no longer even see above it.
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/conversations?limit=50")) return jsonResponse(CONVERSATIONS_PAGE);
      if (u.includes("/api/admin/conversations?q=")) return jsonResponse({ items: [CONVO_GUEST], limit: 50, offset: 0, total: 1 });
      if (u.endsWith("/api/admin/conversations/c1")) return jsonResponse(CONVO_USER_DETAIL_TWO_RUNS);
      throw new Error(`unexpected call: ${u}`);
    });

    renderConversations();
    await user.click(await screen.findByRole("button", { name: "Printer will not connect" }));
    expect(await screen.findByText("My printer will not connect to the network.")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Search conversations"), "x");

    await vi.waitFor(() => {
      expect(screen.queryByText("My printer will not connect to the network.")).not.toBeInTheDocument();
    });
    expect(screen.queryByText("Transcript")).not.toBeInTheDocument();
  });

  it("renders the transcript beside the span tree of the selected run", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/conversations?limit=50")) return jsonResponse(CONVERSATIONS_PAGE);
      if (u.endsWith("/api/admin/conversations/c1")) return jsonResponse(CONVO_USER_DETAIL_TWO_RUNS);
      if (u.endsWith("/api/admin/runs/r1/trace")) return jsonResponse(TRACE_R1);
      throw new Error(`unexpected call: ${u}`);
    });

    renderConversations();

    await user.click(await screen.findByRole("button", { name: "Printer will not connect" }));

    // Transcript text is on screen (left column).
    expect(await screen.findByText("My printer will not connect to the network.")).toBeInTheDocument();

    // Select the run that produced the transcript's assistant turn.
    await user.click(await screen.findByText("r1"));

    // The span tree for that run now renders too, alongside (not instead
    // of) the transcript that is still visible.
    expect(await screen.findByText("answer")).toBeInTheDocument();
    expect(screen.getByText("My printer will not connect to the network.")).toBeInTheDocument();
  });

  it("renders text sensibly and does not crash on image, tool-result, or unrecognised content blocks", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/conversations?limit=50")) return jsonResponse(CONVERSATIONS_PAGE);
      if (u.endsWith("/api/admin/conversations/c1")) return jsonResponse(CONVO_USER_DETAIL_TWO_RUNS);
      throw new Error(`unexpected call: ${u}`);
    });

    renderConversations();
    await user.click(await screen.findByRole("button", { name: "Printer will not connect" }));

    // The mixed-block assistant message's text block renders as text.
    expect(await screen.findByText("Let me check that for you.")).toBeInTheDocument();
    // Non-text blocks (image, tool_result) and a block kind this screen has
    // never seen before must not crash the render, and the screen is still
    // interactive afterward -- the trailing block proves rendering did not
    // stop partway through the array.
    expect(screen.getByText(/some_future_block_kind/)).toBeInTheDocument();
  });

  it("renders one selectable row per run for a conversation with several runs", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/conversations?limit=50")) return jsonResponse(CONVERSATIONS_PAGE);
      if (u.endsWith("/api/admin/conversations/c1")) return jsonResponse(CONVO_USER_DETAIL_TWO_RUNS);
      if (u.endsWith("/api/admin/runs/r1/trace")) return jsonResponse(TRACE_R1);
      throw new Error(`unexpected call: ${u}`);
    });

    renderConversations();
    await user.click(await screen.findByRole("button", { name: "Printer will not connect" }));

    const row1 = await screen.findByText("r1");
    const row2 = await screen.findByText("r2");
    expect(row1.closest("button")).not.toBeNull();
    expect(row2.closest("button")).not.toBeNull();
    expect(row1.closest("button")).not.toBe(row2.closest("button"));

    // Each is independently selectable -- clicking one loads its own trace.
    await user.click(row1);
    expect(await screen.findByText("answer")).toBeInTheDocument();
  });

  it('renders "no runs recorded" explicitly for a conversation with no runs, not an empty panel', async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/conversations?limit=50")) return jsonResponse(CONVERSATIONS_PAGE);
      if (u.endsWith("/api/admin/conversations/c2")) return jsonResponse(CONVO_GUEST_DETAIL_NO_RUNS);
      throw new Error(`unexpected call: ${u}`);
    });

    renderConversations();
    await user.click(await screen.findByRole("button", { name: "Cannot reset password" }));

    expect(await screen.findByText(/no runs recorded/i)).toBeInTheDocument();
    // Not merely absent -- an explicit sentence, distinguishable from a
    // panel that is empty because nothing has loaded yet.
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("renders a visible banner when a conversation's selected trace is truncated", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/conversations?limit=50")) return jsonResponse(CONVERSATIONS_PAGE);
      if (u.endsWith("/api/admin/conversations/c1")) return jsonResponse(CONVO_USER_DETAIL_TWO_RUNS);
      if (u.endsWith("/api/admin/runs/r1/trace")) return jsonResponse(TRACE_R1_TRUNCATED);
      throw new Error(`unexpected call: ${u}`);
    });

    renderConversations();
    await user.click(await screen.findByRole("button", { name: "Printer will not connect" }));
    await user.click(await screen.findByText("r1"));

    expect(await screen.findByText(/truncated/i)).toBeInTheDocument();
  });

  it("renders a failed conversations fetch as StateBlock's error state, never as an empty table", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/conversations?limit=50")) {
        return jsonResponse({ detail: "Forbidden: admin role required" }, 403);
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderConversations();

    expect(await screen.findByRole("alert")).toHaveTextContent("You do not have access to this.");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("renders an empty conversation list distinguishably from a failed one", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/conversations?limit=50")) {
        return jsonResponse({ items: [], limit: 50, offset: 0, total: 0 });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderConversations();

    expect(await screen.findByText("No conversations recorded yet.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a loading state before the conversations response arrives", async () => {
    let resolveList!: (value: Response) => void;
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/conversations?limit=50")) {
        return new Promise<Response>((resolve) => { resolveList = resolve; });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderConversations();

    expect(await screen.findByRole("status")).toHaveTextContent(/loading/i);
    resolveList(jsonResponse(CONVERSATIONS_PAGE));
    await screen.findByText("Printer will not connect");
  });
});
