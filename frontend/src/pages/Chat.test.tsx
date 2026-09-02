import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Chat } from "./Chat";
import * as authCtx from "../auth/AuthContext";

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

function renderChat(principal: unknown = EMPLOYEE) {
  vi.spyOn(authCtx, "useAuth").mockReturnValue({
    status: "signed-in", principal, login: vi.fn(), loginAsGuest: vi.fn(), logout: vi.fn(),
  } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
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

  it("renders a ticket_created frame as a card linking to the ticket list", async () => {
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

    // No /tickets/:id route exists yet (task 5 owns it) -- the card links
    // to the ticket list, not a per-id deep link.
    const link = await screen.findByRole("link", { name: /ticket tck-000099 created/i });
    expect(link).toHaveAttribute("href", "/tickets");
  });

  it("shows a view-trace link for an admin after done, but not for a non-admin", async () => {
    async function run(principal: unknown) {
      let stream: ReturnType<typeof makeTurnStream> | undefined;
      fetchMock.mockReset();
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

      const view = renderChat(principal);
      await screen.findByText("VPN issue");
      await userEvent.click(screen.getByText("VPN issue"));
      await screen.findByText(/no messages yet/i);
      await userEvent.type(screen.getByLabelText("Message"), "Status?");
      await userEvent.click(screen.getByRole("button", { name: /send/i }));
      await waitFor(() => expect(stream).toBeDefined());

      await act(async () => {
        stream!.send({ type: "token", text: "All good." });
        stream!.send({ type: "done", run_id: "r-42" });
      });

      return view;
    }

    const admin = await run(ADMIN);
    await waitFor(() => expect(screen.getByText(/view trace/i)).toBeInTheDocument());
    admin.unmount();

    const employee = await run(EMPLOYEE);
    await waitFor(() => expect(screen.getByText("All good.")).toBeInTheDocument());
    expect(screen.queryByText(/view trace/i)).not.toBeInTheDocument();
    employee.unmount();
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
});
