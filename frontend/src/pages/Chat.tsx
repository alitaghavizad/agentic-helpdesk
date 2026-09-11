import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { ApiError } from "../api/client";
import * as chat from "../api/endpoints/chat";
import type { Conversation, MessageView } from "../api/endpoints/chat";
import { conversationQueryKey, conversationsQueryKey, useChatTurn } from "../hooks/useChatTurn";
import type { Outcome, ToolRow } from "../hooks/turnReducer";
import { StateBlock, describeError } from "../components/StateBlock";
import { Icon, Spinner } from "../components/Icon";
import { dateTime } from "../lib/format";

/**
 * `content` is the stored Anthropic content-block list exactly as
 * exchanged (backend/app/chat/schemas.py's `MessageView`), or a plain
 * string for the rare row that isn't. Only text blocks render -- image/tool
 * blocks have no place in a transcript bubble.
 */
function messageText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (block && typeof block === "object" && typeof (block as Record<string, unknown>).text === "string") {
          return (block as Record<string, unknown>).text as string;
        }
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }
  return "";
}

function outcomeLabel(outcome: Outcome): { text: string; to?: string } {
  switch (outcome.type) {
    case "ticket_created": {
      // /tickets/:id is now a real route (task 5's Tickets page), so this
      // links straight to the created ticket instead of the list -- falling
      // back to the list only if the frame is somehow missing its id.
      const ticketId = outcome.data.ticket_id;
      const to = typeof ticketId === "string" && ticketId.length > 0 ? `/tickets/${ticketId}` : "/tickets";
      return { text: `Ticket ${outcome.data.ticket_number ?? ""} created`, to };
    }
    case "approval_requested":
      return { text: `Approval ${outcome.data.request_number ?? ""} requested` };
    case "task_recorded":
      return { text: `Task recorded: ${outcome.data.title ?? ""}` };
    case "attachment_request":
      return { text: `Attachment needed: ${outcome.data.reason ?? ""}` };
    default:
      return { text: "" };
  }
}

const TOOL_TONE: Record<ToolRow["status"], string> = {
  running: "border-line bg-surface-2 text-ink-3",
  ok: "border-tone-success-line bg-tone-success-bg text-tone-success-fg",
  error: "border-tone-danger-line bg-tone-danger-bg text-tone-danger-fg",
};

function ToolRowView({ tool }: { tool: ToolRow }) {
  const label = tool.status === "running" ? "running" : tool.status === "ok" ? "done" : "failed";
  return (
    <li
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs ${TOOL_TONE[tool.status]}`}
    >
      {tool.status === "running" ? (
        <Spinner className="size-3" />
      ) : (
        <Icon name={tool.status === "ok" ? "check" : "alert"} className="size-3" />
      )}
      used <span className="font-medium">{tool.name}</span> ({label})
    </li>
  );
}

/** Avatar disc beside a transcript bubble -- "you" or the agent. */
function Avatar({ role }: { role: "user" | "assistant" }) {
  return (
    <span
      aria-hidden="true"
      className={`mt-0.5 grid size-7 shrink-0 place-items-center rounded-full text-[10px] font-semibold tracking-tight ${
        role === "user" ? "bg-surface-3 text-ink-2" : "bg-brand text-on-brand"
      }`}
    >
      {role === "user" ? "YOU" : <Icon name="sparkles" className="size-3.5" />}
    </span>
  );
}

function ConversationList({
  conversations, selectedId, onSelect,
}: {
  conversations: Conversation[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (conversations.length === 0) {
    return <StateBlock status="empty" emptyLabel="No conversations yet. Start one above." />;
  }
  return (
    <ul className="space-y-1">
      {conversations.map((conversation) => (
        <li key={conversation.id}>
          <button
            type="button"
            onClick={() => onSelect(conversation.id)}
            aria-current={conversation.id === selectedId}
            className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition ${
              conversation.id === selectedId
                ? "bg-brand-soft font-medium text-brand-ink"
                : "text-ink-2 hover:bg-surface-2 hover:text-ink"
            }`}
          >
            <Icon name="message" className="size-3.5 shrink-0 opacity-70" />
            <span className="truncate">{conversation.title ?? "Untitled conversation"}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

export function Chat() {
  const { principal } = useAuth();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [attachmentName, setAttachmentName] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const conversationsQuery = useQuery({ queryKey: conversationsQueryKey, queryFn: chat.listConversations });
  const conversationQuery = useQuery({
    queryKey: conversationQueryKey(selectedId ?? ""),
    queryFn: () => chat.getConversation(selectedId as string),
    enabled: selectedId !== null,
  });

  const { turn, busy, send, reset } = useChatTurn(selectedId);
  const [pendingUserContent, setPendingUserContent] = useState<string | null>(null);
  // The message ids present the moment a send started, captured so the
  // optimistic user bubble can tell "a NEW user message landed" apart from
  // "the transcript I already knew about got refetched." Not state: writing
  // it must never itself trigger a render.
  const priorMessageIdsRef = useRef<Set<string>>(new Set());

  // Switching conversations must not leave a previous conversation's live
  // turn (or its optimistic user bubble) bleeding into the newly selected
  // one's transcript panel.
  useEffect(() => {
    reset();
    setPendingUserContent(null);
    priorMessageIdsRef.current = new Set();
  }, [selectedId, reset]);

  const messages: MessageView[] = conversationQuery.data?.messages ?? [];

  // Whether THIS turn's answer is already in the refetched transcript --
  // computed straight from data, not from a timestamp. An earlier version
  // gated this on `conversationQuery.dataUpdatedAt` advancing past a
  // timestamp recorded from an effect; when the invalidated refetch
  // resolved before React committed the `done` render, that comparison was
  // never satisfied and the live bubble (answer + trace link) rendered
  // forever alongside the now-identical stored message. Reading the
  // transcript directly has no such window: it is simply true or false on
  // every render, synchronously, from whatever `messages` currently holds.
  const turnPersisted = turn.done && turn.runId !== null
    && messages.some((message) => message.run_id === turn.runId);

  // Whether the OPTIMISTIC USER bubble's own message is now in the stored
  // transcript. Deliberately NOT gated on `turnPersisted`: the backend
  // commits the user's message synchronously, before the turn even starts
  // running (backend/app/chat/router.py's send_message_endpoint stages and
  // commits it up front) -- long before the assistant's answer is
  // persisted, and TanStack Query's default `refetchOnWindowFocus` means the
  // transcript can refetch and pick up that user message mid-turn, well
  // before `turnPersisted` ever becomes true. Gating on `turnPersisted` left
  // a window where the transcript already had the real user message AND the
  // optimistic bubble was still showing it -- rendered twice. Comparing
  // against the message ids captured right before this send started (rather
  // than matching on content, which a duplicate could coincidentally share)
  // is what makes this correct regardless of when or how many times a
  // refetch lands.
  const userMessagePersisted = pendingUserContent !== null
    && messages.some((message) => message.role === "user" && !priorMessageIdsRef.current.has(message.id));

  // Once the user's own message is persisted, the optimistic bubble showing
  // it is no longer needed.
  useEffect(() => {
    if (userMessagePersisted) setPendingUserContent(null);
  }, [userMessagePersisted]);

  async function handleNewConversation() {
    const created = await chat.createConversation();
    await queryClient.invalidateQueries({ queryKey: conversationsQueryKey });
    setSelectedId(created.id);
  }

  async function handleSend(event: FormEvent) {
    event.preventDefault();
    const content = draft.trim();
    if (!content || !selectedId || busy) return;
    setDraft("");
    // Optimistic: the composer clears immediately, but a turn can run for
    // tens of seconds, and the user's own question should not vanish from
    // the transcript for that whole window just because the stored
    // transcript hasn't been refetched yet. Snapshot which message ids
    // already exist so `userMessagePersisted` can recognise the real one
    // landing, however soon a refetch brings it in.
    priorMessageIdsRef.current = new Set(messages.map((message) => message.id));
    setPendingUserContent(content);
    await send(content);
  }

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || !selectedId) return;
    setAttachmentError(null);
    setUploading(true);
    try {
      const uploaded = await chat.uploadAttachment(selectedId, file);
      setAttachmentName(uploaded.filename);
    } catch (err) {
      setAttachmentError(err instanceof ApiError ? err.detail : "Upload failed. Please try again.");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  const showLiveTurn = !turnPersisted
    && (busy || turn.text.length > 0 || turn.tools.length > 0 || turn.outcomes.length > 0 || turn.error !== null);
  const showPendingUser = pendingUserContent !== null && !userMessagePersisted;
  const selectedTitle = conversationQuery.data?.title ?? "Untitled conversation";

  return (
    <div className="grid grid-cols-1 gap-6 md:grid-cols-[17rem_1fr]">
      <aside className="space-y-3">
        <button type="button" onClick={handleNewConversation} className="btn-primary w-full py-2">
          <Icon name="plus" className="size-4" />
          New conversation
        </button>
        {conversationsQuery.isLoading ? (
          <StateBlock status="loading" />
        ) : conversationsQuery.isError ? (
          <StateBlock status="error" message={describeError(conversationsQuery.error)} />
        ) : (
          <ConversationList
            conversations={conversationsQuery.data ?? []}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        )}
      </aside>

      <section className="card flex min-h-[32rem] flex-col overflow-hidden">
        {selectedId === null ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
            <span aria-hidden="true" className="grid size-12 place-items-center rounded-full bg-brand-soft text-brand-ink">
              <Icon name="sparkles" className="size-5" />
            </span>
            <p className="text-sm font-medium text-ink">Select a conversation, or start a new one.</p>
            <p className="max-w-xs text-sm text-ink-3">
              Ask about access, hardware, accounts or policy — the agent answers from Northstar's own
              documentation.
            </p>
          </div>
        ) : (
          <>
            <header className="flex items-center gap-2 border-b border-line px-4 py-3">
              <Icon name="message" className="size-4 shrink-0 text-ink-3" />
              <h1 className="truncate text-sm font-semibold text-ink">{selectedTitle}</h1>
              {busy && (
                <span className="ml-auto flex items-center gap-1.5 text-xs font-medium text-ink-3">
                  <Spinner className="size-3 text-brand" />
                  Thinking…
                </span>
              )}
            </header>

            <div className="flex-1 space-y-4 overflow-y-auto p-4">
              {conversationQuery.isLoading ? (
                <StateBlock status="loading" />
              ) : conversationQuery.isError ? (
                <StateBlock status="error" message={describeError(conversationQuery.error)} />
              ) : messages.length === 0 && !showLiveTurn && !showPendingUser ? (
                <StateBlock status="empty" emptyLabel="No messages yet. Say hello below." />
              ) : (
                <ul className="space-y-5">
                  {messages.map((message) => (
                    <li
                      key={message.id}
                      className={`flex animate-fade gap-2.5 ${message.role === "user" ? "flex-row-reverse" : ""}`}
                    >
                      <Avatar role={message.role === "user" ? "user" : "assistant"} />
                      <div className={`min-w-0 max-w-[80%] ${message.role === "user" ? "items-end text-right" : ""}`}>
                        <p className="mb-1 text-xs text-ink-3">
                          {message.role} · {dateTime(message.created_at)}
                        </p>
                        <div
                          className={`inline-block rounded-2xl px-3.5 py-2.5 text-left text-sm ${
                            message.role === "user"
                              ? "rounded-tr-sm bg-brand text-on-brand"
                              : "rounded-tl-sm border border-line bg-surface-2 text-ink"
                          }`}
                        >
                          <p className="whitespace-pre-wrap">{messageText(message.content)}</p>
                        </div>
                        {message.role === "assistant" && message.run_id && principal?.role === "admin" && (
                          <Link
                            to={`/admin/traces/${message.run_id}`}
                            className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-brand-ink hover:underline"
                          >
                            <Icon name="activity" className="size-3" />
                            View trace
                          </Link>
                        )}
                      </div>
                    </li>
                  ))}

                  {showPendingUser && (
                    <li className="flex animate-fade flex-row-reverse gap-2.5" aria-label="you, sending">
                      <Avatar role="user" />
                      <div className="min-w-0 max-w-[80%] text-right">
                        <p className="mb-1 text-xs text-ink-3">you</p>
                        <div className="inline-block rounded-2xl rounded-tr-sm bg-brand px-3.5 py-2.5 text-left text-sm text-on-brand opacity-80">
                          <p className="whitespace-pre-wrap">{pendingUserContent}</p>
                        </div>
                      </div>
                    </li>
                  )}

                  {showLiveTurn && (
                    <li className="flex animate-fade gap-2.5" aria-label="assistant, streaming">
                      <Avatar role="assistant" />
                      <div className="min-w-0 max-w-[80%]">
                        <p className="mb-1 text-xs text-ink-3">assistant</p>
                        <div className="rounded-2xl rounded-tl-sm border border-line bg-surface-2 px-3.5 py-2.5 text-sm text-ink">
                          {turn.text ? (
                            <p className="whitespace-pre-wrap">{turn.text}</p>
                          ) : (
                            <span className="flex gap-1" aria-hidden="true">
                              <span className="size-1.5 animate-pulse-soft rounded-full bg-ink-3" />
                              <span className="size-1.5 animate-pulse-soft rounded-full bg-ink-3 [animation-delay:200ms]" />
                              <span className="size-1.5 animate-pulse-soft rounded-full bg-ink-3 [animation-delay:400ms]" />
                            </span>
                          )}

                          {turn.tools.length > 0 && (
                            <ul className="mt-2.5 flex flex-wrap gap-1.5">
                              {turn.tools.map((tool) => (
                                <ToolRowView key={tool.id} tool={tool} />
                              ))}
                            </ul>
                          )}

                          {turn.outcomes.length > 0 && (
                            <ul className="mt-2.5 space-y-1.5">
                              {turn.outcomes.map((outcome, index) => {
                                const { text, to } = outcomeLabel(outcome);
                                return (
                                  <li
                                    key={`${outcome.type}-${index}`}
                                    className="flex items-center gap-1.5 rounded-lg border border-brand-line bg-brand-soft px-2.5 py-1.5 text-xs text-brand-ink"
                                  >
                                    <Icon name="check" className="size-3 shrink-0" />
                                    {to ? <Link to={to} className="font-medium hover:underline">{text}</Link> : text}
                                  </li>
                                );
                              })}
                            </ul>
                          )}

                          {turn.error && (
                            <p
                              role="alert"
                              className="mt-2.5 flex items-center gap-1.5 rounded-lg border border-tone-danger-line bg-tone-danger-bg px-2.5 py-1.5 text-xs font-medium text-tone-danger-fg"
                            >
                              <Icon name="alert" className="size-3 shrink-0" />
                              {turn.error}
                            </p>
                          )}
                        </div>

                        {turn.done && turn.runId && principal?.role === "admin" && (
                          <Link
                            to={`/admin/traces/${turn.runId}`}
                            className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-brand-ink hover:underline"
                          >
                            <Icon name="activity" className="size-3" />
                            View trace
                          </Link>
                        )}
                      </div>
                    </li>
                  )}
                </ul>
              )}
            </div>

            <form onSubmit={handleSend} className="border-t border-line bg-surface-2/50 p-3">
              {attachmentError && (
                <p
                  role="alert"
                  className="mb-2 flex items-center gap-1.5 rounded-lg border border-tone-danger-line bg-tone-danger-bg px-2.5 py-1.5 text-xs font-medium text-tone-danger-fg"
                >
                  <Icon name="alert" className="size-3 shrink-0" />
                  {attachmentError}
                </p>
              )}
              {attachmentName && !attachmentError && (
                <p className="mb-2 flex items-center gap-1.5 text-xs text-ink-3">
                  <Icon name="paperclip" className="size-3" />
                  Attached: {attachmentName}
                </p>
              )}
              <div className="flex items-end gap-2">
                <label
                  className={`btn-secondary size-9 shrink-0 p-0 ${busy || uploading ? "pointer-events-none opacity-50" : "cursor-pointer"}`}
                  title="Attach a file"
                >
                  {uploading ? <Spinner className="size-4" /> : <Icon name="paperclip" className="size-4" />}
                  <input
                    ref={fileInputRef}
                    type="file"
                    aria-label="Attach a file"
                    onChange={handleFileChange}
                    disabled={busy || uploading}
                    className="sr-only"
                  />
                </label>
                <textarea
                  aria-label="Message"
                  value={draft}
                  placeholder="Describe your issue…"
                  onChange={(event) => setDraft(event.target.value)}
                  disabled={busy}
                  rows={2}
                  className="field-input flex-1 resize-none"
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      handleSend(event);
                    }
                  }}
                />
                <button
                  type="submit"
                  disabled={busy || draft.trim().length === 0}
                  className="btn-primary h-9 shrink-0"
                >
                  {busy ? <Spinner className="size-4" /> : <Icon name="send" className="size-4" />}
                  {busy ? "Sending…" : "Send"}
                </button>
              </div>
            </form>
          </>
        )}
      </section>
    </div>
  );
}
