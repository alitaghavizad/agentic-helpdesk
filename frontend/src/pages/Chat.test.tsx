import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Chat } from "./Chat";
import * as authCtx from "../auth/AuthContext";
import { conversationQueryKey } from "../hooks/useChatTurn";

// fetch is stubbed directly, the same way client.test.ts and
// useNotifications.test.tsx do it -- MSW is not used anywhere else in this
// project, and this test hinges on controlling exactly which bytes arrive
// on the turn stream's body, which a fetch stub does more directly.
const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/**
 * An open `text/event-stream` Response the test drives by hand: `send`
 * pushes one frame, `close` ends the stream (triggering `done: true` on the
 * reader's next read). `signal`, when supplied, is wired to actually error
 * the stream's reader on abort -- the way a real aborted `fetch` does --
 * mirroring useNotifications.test.tsx's `openStreamResponse`.
 */
function makeTurnStream(signal?: AbortSignal) {
  const encoder = new TextEncoder();
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      ctrl = controller;
    },
  });
  signal?.addEventListener("abort", () => {
    try {
      ctrl.error(new DOMException("The operation was aborted.", "AbortError"));
    } catch {
      // Already closed/errored.
    }
  });
  return {
    response: new Response(body, { headers: { "content-type": "text/event-stream" } }),
    send(frame: Record<string, unknown>) {
      ctrl.enqueue(encoder.encode(`data: ${JSON.stringify(frame)}\n\n`));
    },
    close() {
      try {
        ctrl.close();
      } catch {
        // Already closed.
      }
    },
  };
}

const ADMIN = {
  kind: "user", user_id: "u1", role: "admin", clearance: "privileged",
  department: "IT", employee_ref: null, helpdesk_ref: null,
  username: "admin", full_name: "Administrator",
};
const EMPLOYEE = {
  kind: "user", user_id: "u2", role: "employee", clearance: "standard",
  department: "Engineering", employee_ref: "EMP-1", helpdesk_ref: null,
  username: "j.doe", full_name: "Jane Doe",
};

const CONV_LIST = [
  { id: "c1", title: "VPN issue", status: "open", messages: [] },
  { id: "c2", title: "Password reset", status: "open", messages: [] },
];

function conversationDetail(id: string, messages: unknown[] = []) {
  return { id, title: "VPN issue", status: "open", messages };
}

function renderChat(principal: unknown = EMPLOYEE, client?: QueryClient) {
  vi.spyOn(authCtx, "useAuth").mockReturnValue({
    status: "signed-in", principal, login: vi.fn(), loginAsGuest: vi.fn(), logout: vi.fn(),
  } as never);
  const queryClient = client ?? new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Chat />
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

describe("Chat", () => {
  it("renders the conversation list from GET /api/conversations", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/conversations")) return jsonResponse(CONV_LIST);
      throw new Error(`unexpected call: ${url}`);
    });

    renderChat();

    expect(await screen.findByText("VPN issue")).toBeInTheDocument();
    expect(screen.getByText("Password reset")).toBeInTheDocument();
  });

  it("selecting a conversation renders its stored transcript from GET /api/conversations/{id}, not the list's empty messages", async () => {
    // The list endpoint's `messages` is ALWAYS [] -- if the page rendered
    // from that instead of fetching the by-id endpoint, these two strings
    // would never appear.
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/conversations")) return jsonResponse(CONV_LIST);
      if (u.endsWith("/api/conversations/c1") && (!init || init.method === undefined)) {
        return jsonResponse(conversationDetail("c1", [
          { id: "m1", role: "user", content: "My VPN is down", created_at: "2026-09-01T10:00:00Z", run_id: null },
          { id: "m2", role: "assistant", content: [{ type: "text", text: "Let's take a look." }], created_at: "2026-09-01T10:00:05Z", run_id: "r0" },
        ]));
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderChat();
    await screen.findByText("VPN issue");
    await userEvent.click(screen.getByText("VPN issue"));

    expect(await screen.findByText("My VPN is down")).toBeInTheDocument();
    expect(screen.getByText("Let's take a look.")).toBeInTheDocument();
  });

  it("sending a message streams tokens into a growing assistant bubble", async () => {
    let stream: ReturnType<typeof makeTurnStream> | undefined;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/conversations")) return jsonResponse(CONV_LIST);
      if (u.endsWith("/api/conversations/c1") && init?.method === undefined) {
        return jsonResponse(conversationDetail("c1", []));
      }
      if (u.endsWith("/api/conversations/c1/messages") && init?.method === "POST") {
        stream = makeTurnStream((init.signal as AbortSignal) ?? undefined);
        return stream.response;
      }
      throw new Error(`unexpected call: ${u} ${init?.method}`);
    });

    renderChat();
    await screen.findByText("VPN issue");
    await userEvent.click(screen.getByText("VPN issue"));
    await screen.findByText(/no messages yet/i);

    await userEvent.type(screen.getByLabelText("Message"), "Hi there");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));

    // The typed message must not vanish from the transcript for the whole
    // turn just because the stored transcript hasn't refetched yet -- a
    // turn can run for tens of seconds.
    expect(await screen.findByText("Hi there")).toBeInTheDocument();

    await waitFor(() => expect(stream).toBeDefined());
    await act(async () => {
      stream!.send({ type: "token", text: "Hel" });
    });
    expect(await screen.findByText("Hel")).toBeInTheDocument();

    await act(async () => {
      stream!.send({ type: "token", text: "lo" });
    });
    await waitFor(() => expect(screen.getByText("Hello")).toBeInTheDocument());

    await act(async () => {
      stream!.send({ type: "done", run_id: "r1" });
      stream!.close();
    });
  });

  it("renders a ticket_created frame as a card linking to the created ticket", async () => {
    let stream: ReturnType<typeof makeTurnStream> | undefined;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/conversations")) return jsonResponse(CONV_LIST);
      if (u.endsWith("/api/conversations/c1") && init?.method === undefined) {
        return jsonResponse(conversationDetail("c1", []));
      }
      if (u.endsWith("/api/conversations/c1/messages") && init?.method === "POST") {
        stream = makeTurnStream((init.signal as AbortSignal) ?? undefined);
        return stream.response;
      }
      throw new Error(`unexpected call: ${u} ${init?.method}`);
    });

    renderChat();
    await screen.findByText("VPN issue");
    await userEvent.click(screen.getByText("VPN issue"));
    await screen.findByText(/no messages yet/i);
    await userEvent.type(screen.getByLabelText("Message"), "Please open a ticket");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(stream).toBeDefined());

    await act(async () => {
      stream!.send({ type: "ticket_created", ticket_number: "TCK-000099", ticket_id: "tid1" });
    });

    // Task 5 adds the /tickets/:id route -- the card must deep-link to the
    // created ticket by id, not to the list.
    const link = await screen.findByRole("link", { name: /ticket tck-000099 created/i });
    expect(link).toHaveAttribute("href", "/tickets/tid1");
  });

  it("falls back to the ticket list when a ticket_created frame is somehow missing its id", async () => {
    let stream: ReturnType<typeof makeTurnStream> | undefined;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/conversations")) return jsonResponse(CONV_LIST);
      if (u.endsWith("/api/conversations/c1") && init?.method === undefined) {
        return jsonResponse(conversationDetail("c1", []));
      }
      if (u.endsWith("/api/conversations/c1/messages") && init?.method === "POST") {
        stream = makeTurnStream((init.signal as AbortSignal) ?? undefined);
        return stream.response;
      }
      throw new Error(`unexpected call: ${u} ${init?.method}`);
    });

    renderChat();
    await screen.findByText("VPN issue");
    await userEvent.click(screen.getByText("VPN issue"));
    await screen.findByText(/no messages yet/i);
    await userEvent.type(screen.getByLabelText("Message"), "Please open a ticket");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(stream).toBeDefined());

    await act(async () => {
      stream!.send({ type: "ticket_created", ticket_number: "TCK-000100" });
    });

    const link = await screen.findByRole("link", { name: /ticket tck-000100 created/i });
    expect(link).toHaveAttribute("href", "/tickets");
  });

  it("renders a completed turn's answer and trace link exactly once, even when the invalidated refetch resolves immediately", async () => {
    // Regression for a race in an earlier version: the reset that hides the
    // live bubble once a turn's answer lands in the stored transcript used
    // to be gated on `conversationQuery.dataUpdatedAt` advancing past a
    // timestamp captured from an effect. When the invalidated refetch
    // resolved before React committed the `done` render (exactly what
    // happens here -- every mocked call in this suite resolves on the next
    // microtask, same as a fast local backend would), that comparison was
    // never satisfied and the live bubble never cleared: the completed
    // answer and the admin's trace link both rendered twice, permanently.
    // This also covers Step 3's "admin sees a view-trace link after done,
    // a non-admin does not," now pinned against a fixture that actually
    // persists the turn (rather than one where the refetch stays empty
    // forever, which cannot detect this race).
    async function run(principal: unknown) {
      let stream: ReturnType<typeof makeTurnStream> | undefined;
      let detailCalls = 0;
      fetchMock.mockReset();
      fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
        const u = String(url);
        if (u.endsWith("/api/conversations")) return jsonResponse(CONV_LIST);
        if (u.endsWith("/api/conversations/c1") && init?.method === undefined) {
          detailCalls += 1;
          if (detailCalls === 1) return jsonResponse(conversationDetail("c1", []));
          // The refetch `done` triggers, with this turn's answer now persisted.
          return jsonResponse(conversationDetail("c1", [
            { id: "m-user", role: "user", content: "Status?", created_at: "2026-09-02T09:00:00Z", run_id: null },
            { id: "m-assistant", role: "assistant", content: [{ type: "text", text: "All good." }], created_at: "2026-09-02T09:00:05Z", run_id: "r-77" },
          ]));
        }
        if (u.endsWith("/api/conversations/c1/messages") && init?.method === "POST") {
          stream = makeTurnStream((init.signal as AbortSignal) ?? undefined);
          return stream.response;
        }
        throw new Error(`unexpected call: ${u} ${init?.method}`);
      });

      const view = renderChat(principal);
      await screen.findByText("VPN issue");
      await userEvent.click(screen.getByText("VPN issue"));
      await screen.findByText(/no messages yet/i);
      await userEvent.type(screen.getByLabelText("Message"), "Status?");
      await userEvent.click(screen.getByRole("button", { name: /send/i }));
      await waitFor(() => expect(stream).toBeDefined());

      await act(async () => {
        stream!.send({ type: "token", text: "All good." });
        stream!.send({ type: "done", run_id: "r-77" });
        stream!.close();
      });

      // Let the invalidated query's refetch resolve and commit.
      await waitFor(() => expect(detailCalls).toBeGreaterThan(1));

      return view;
    }

    const admin = await run(ADMIN);
    await waitFor(() => expect(screen.getAllByText("All good.")).toHaveLength(1));
    expect(screen.getAllByText(/view trace/i)).toHaveLength(1);
    admin.unmount();

    const employee = await run(EMPLOYEE);
    await waitFor(() => expect(screen.getAllByText("All good.")).toHaveLength(1));
    expect(screen.queryAllByText(/view trace/i)).toHaveLength(0);
    employee.unmount();
  });

  it("renders a view-trace link from a persisted message's own run_id for an admin, with no live turn involved", async () => {
    // The two-source trace-link design's whole justification is that this
    // link survives a reload -- i.e. it must come from `message.run_id` on
    // the stored transcript alone, not from any turn state.
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/conversations")) return jsonResponse(CONV_LIST);
      if (u.endsWith("/api/conversations/c1") && init?.method === undefined) {
        return jsonResponse(conversationDetail("c1", [
          { id: "m1", role: "user", content: "Ping", created_at: "2026-09-01T10:00:00Z", run_id: null },
          { id: "m2", role: "assistant", content: [{ type: "text", text: "Pong" }], created_at: "2026-09-01T10:00:05Z", run_id: "r-99" },
        ]));
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderChat(ADMIN);
    await screen.findByText("VPN issue");
    await userEvent.click(screen.getByText("VPN issue"));

    await screen.findByText("Pong");
    expect(screen.getByText(/view trace/i)).toBeInTheDocument();
  });

  it("keeps the partial answer on screen when an error arrives mid-turn", async () => {
    // The backend emits `error` mid-turn (budget exceeded, refusal) and
    // still always emits `done` afterward (its `finally` block) -- but the
    // text streamed before the error must stay next to it, not be replaced
    // or blanked. turnReducer.test.ts pins the state; this pins the render.
    let stream: ReturnType<typeof makeTurnStream> | undefined;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/conversations")) return jsonResponse(CONV_LIST);
      if (u.endsWith("/api/conversations/c1") && init?.method === undefined) {
        return jsonResponse(conversationDetail("c1", []));
      }
      if (u.endsWith("/api/conversations/c1/messages") && init?.method === "POST") {
        stream = makeTurnStream((init.signal as AbortSignal) ?? undefined);
        return stream.response;
      }
      throw new Error(`unexpected call: ${u} ${init?.method}`);
    });

    renderChat();
    await screen.findByText("VPN issue");
    await userEvent.click(screen.getByText("VPN issue"));
    await screen.findByText(/no messages yet/i);
    await userEvent.type(screen.getByLabelText("Message"), "Budget check");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(stream).toBeDefined());

    await act(async () => {
      stream!.send({ type: "token", text: "Partial answer" });
      stream!.send({ type: "error", message: "Turn ended: budget." });
      stream!.send({ type: "done", run_id: "r-err" });
      stream!.close();
    });

    expect(await screen.findByText("Partial answer")).toBeInTheDocument();
    expect(screen.getByText("Turn ended: budget.")).toBeInTheDocument();
  });

  it("disables the composer while a turn is in flight", async () => {
    let stream: ReturnType<typeof makeTurnStream> | undefined;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/conversations")) return jsonResponse(CONV_LIST);
      if (u.endsWith("/api/conversations/c1") && init?.method === undefined) {
        return jsonResponse(conversationDetail("c1", []));
      }
      if (u.endsWith("/api/conversations/c1/messages") && init?.method === "POST") {
        stream = makeTurnStream((init.signal as AbortSignal) ?? undefined);
        return stream.response;
      }
      throw new Error(`unexpected call: ${u} ${init?.method}`);
    });

    renderChat();
    await screen.findByText("VPN issue");
    await userEvent.click(screen.getByText("VPN issue"));
    await screen.findByText(/no messages yet/i);

    const textbox = screen.getByLabelText("Message");
    await userEvent.type(textbox, "Hi");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(stream).toBeDefined());
    await waitFor(() => expect(textbox).toBeDisabled());
    expect(screen.getByRole("button", { name: /sending/i })).toBeDisabled();

    await act(async () => {
      stream!.send({ type: "done", run_id: "r1" });
      stream!.close();
    });

    await waitFor(() => expect(textbox).not.toBeDisabled());
  });

  it("surfaces the server's size-limit message on a 413 attachment upload", async () => {
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/conversations")) return jsonResponse(CONV_LIST);
      if (u.endsWith("/api/conversations/c1") && init?.method === undefined) {
        return jsonResponse(conversationDetail("c1", []));
      }
      if (u.endsWith("/api/conversations/c1/attachments") && init?.method === "POST") {
        return jsonResponse({ detail: "attachment exceeds the 10MB limit" }, 413);
      }
      throw new Error(`unexpected call: ${u} ${init?.method}`);
    });

    renderChat();
    await screen.findByText("VPN issue");
    await userEvent.click(screen.getByText("VPN issue"));
    await screen.findByText(/no messages yet/i);

    const file = new File(["x".repeat(20)], "big.png", { type: "image/png" });
    const input = screen.getByLabelText(/attach a file/i) as HTMLInputElement;
    await userEvent.upload(input, file);

    expect(await screen.findByText("attachment exceeds the 10MB limit")).toBeInTheDocument();
  });

  it("aborts the in-flight turn's stream on unmount", async () => {
    // A leaked stream per navigation away from /chat mid-turn is exactly
    // what tying the AbortController to unmount exists to prevent (a
    // global constraint of this phase).
    let capturedSignal: AbortSignal | undefined;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/conversations")) return jsonResponse(CONV_LIST);
      if (u.endsWith("/api/conversations/c1") && init?.method === undefined) {
        return jsonResponse(conversationDetail("c1", []));
      }
      if (u.endsWith("/api/conversations/c1/messages") && init?.method === "POST") {
        capturedSignal = init?.signal ?? undefined;
        return makeTurnStream(capturedSignal).response;
      }
      throw new Error(`unexpected call: ${u} ${init?.method}`);
    });

    const view = renderChat();
    await screen.findByText("VPN issue");
    await userEvent.click(screen.getByText("VPN issue"));
    await screen.findByText(/no messages yet/i);
    await userEvent.type(screen.getByLabelText("Message"), "Hi");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(capturedSignal).toBeDefined());
    expect(capturedSignal?.aborted).toBe(false);

    view.unmount();

    expect(capturedSignal?.aborted).toBe(true);
  });

  it("does not render the user's own message twice when the conversation refetches mid-turn", async () => {
    // Regression: the optimistic user bubble used to clear on the
    // ASSISTANT's persistence (`turnPersisted`), not the user message's own.
    // The backend commits the user's message synchronously, before the turn
    // even starts running (backend/app/chat/router.py's
    // send_message_endpoint stages and commits it up front) -- and
    // TanStack Query's default `refetchOnWindowFocus` (main.tsx's
    // `new QueryClient()` does not disable it) means the transcript can
    // refetch and pick up that user message mid-turn, long before the
    // assistant's answer -- and `turnPersisted` -- ever exist. Simulating
    // that refetch directly against the test's own QueryClient (rather than
    // firing a real focus event) isolates exactly this: a refetch landing
    // while `pendingUserContent` is still set and the turn is still busy.
    let stream: ReturnType<typeof makeTurnStream> | undefined;
    let detailCalls = 0;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/conversations")) return jsonResponse(CONV_LIST);
      if (u.endsWith("/api/conversations/c1") && init?.method === undefined) {
        detailCalls += 1;
        if (detailCalls === 1) return jsonResponse(conversationDetail("c1", []));
        // The mid-turn refetch: the user's message is already committed;
        // the assistant has not answered yet.
        return jsonResponse(conversationDetail("c1", [
          { id: "m-user", role: "user", content: "Ping mid-turn", created_at: "2026-09-02T09:00:00Z", run_id: null },
        ]));
      }
      if (u.endsWith("/api/conversations/c1/messages") && init?.method === "POST") {
        stream = makeTurnStream((init.signal as AbortSignal) ?? undefined);
        return stream.response;
      }
      throw new Error(`unexpected call: ${u} ${init?.method}`);
    });

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderChat(EMPLOYEE, client);
    await screen.findByText("VPN issue");
    await userEvent.click(screen.getByText("VPN issue"));
    await screen.findByText(/no messages yet/i);
    await userEvent.type(screen.getByLabelText("Message"), "Ping mid-turn");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(stream).toBeDefined());

    // The turn is still in flight -- no `done` sent yet -- when the
    // transcript refetches (e.g. the tab regains focus).
    await act(async () => {
      await client.refetchQueries({ queryKey: conversationQueryKey("c1") });
    });
    await waitFor(() => expect(detailCalls).toBeGreaterThan(1));

    expect(screen.getAllByText("Ping mid-turn")).toHaveLength(1);

    await act(async () => {
      stream!.send({ type: "done", run_id: "r-1" });
      stream!.close();
    });
  });

  it("does not let a done frame with no run_id false-match a stored message's null run_id", async () => {
    // The errored-turn path: a turn that fails can reach `done` with no
    // `run_id` at all. Without the `turn.runId !== null` guard in
    // `turnPersisted`, that null would coincidentally match ANY stored
    // message with `run_id: null` -- which every plain user message has --
    // and the live bubble (including whatever the assistant said before
    // things went wrong) would vanish even though nothing from THIS turn
    // was ever actually persisted.
    let stream: ReturnType<typeof makeTurnStream> | undefined;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/conversations")) return jsonResponse(CONV_LIST);
      if (u.endsWith("/api/conversations/c1") && init?.method === undefined) {
        // A prior turn's stored user message -- run_id: null, same as any
        // user row -- present before this turn even starts.
        return jsonResponse(conversationDetail("c1", [
          { id: "m-prior-user", role: "user", content: "Earlier question", created_at: "2026-09-01T09:00:00Z", run_id: null },
        ]));
      }
      if (u.endsWith("/api/conversations/c1/messages") && init?.method === "POST") {
        stream = makeTurnStream((init.signal as AbortSignal) ?? undefined);
        return stream.response;
      }
      throw new Error(`unexpected call: ${u} ${init?.method}`);
    });

    renderChat();
    await screen.findByText("VPN issue");
    await userEvent.click(screen.getByText("VPN issue"));
    await screen.findByText("Earlier question");
    await userEvent.type(screen.getByLabelText("Message"), "New question");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(stream).toBeDefined());

    await act(async () => {
      stream!.send({ type: "token", text: "Still working on it" });
      stream!.send({ type: "done" }); // no run_id at all
      stream!.close();
    });

    // Nothing about this turn was ever persisted (the refetch fixture above
    // never changes), so the answer must stay visible -- hiding it here
    // would be acting on a false "already saved" signal from a
    // coincidental null-run_id match.
    await waitFor(() => expect(screen.getByText("Still working on it")).toBeInTheDocument());
  });
});
