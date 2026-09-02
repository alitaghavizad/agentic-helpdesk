import { apiFetch, apiStream } from "../client";
import type { components } from "../schema";

export type Conversation = components["schemas"]["ConversationResponse"];
export type MessageView = components["schemas"]["MessageView"];
export type Attachment = components["schemas"]["AttachmentResponse"];

/**
 * `GET /api/conversations`. `messages` on every row here is ALWAYS `[]`
 * (backend/app/chat/router.py's `_serialize` defaults it and only the by-id
 * endpoint below ever passes a transcript) -- a sidebar must not read the
 * whole message table to render titles. The generated type does not show
 * this; do not use this list as a source of transcript content.
 */
export const listConversations = () => apiFetch<Conversation[]>("/api/conversations");

/** `GET /api/conversations/{id}` -- the only endpoint that populates `messages`. */
export const getConversation = (id: string) => apiFetch<Conversation>(`/api/conversations/${id}`);

export const createConversation = (title?: string | null) =>
  apiFetch<Conversation>("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ title: title ?? null }),
  });

/**
 * `POST /api/conversations/{id}/messages` is SSE over POST, not a JSON
 * response -- openapi-typescript types its body as `unknown` because
 * FastAPI's schema has no way to describe a `StreamingResponse`. Never
 * `EventSource`: this route needs a POST body and an `Authorization` header,
 * neither of which `EventSource` can send. Never retried or resumed (spec
 * §6.3) -- callers read the response once through `readSse` and give up if
 * it drops.
 */
export const sendMessage = (conversationId: string, content: string, init: RequestInit = {}) =>
  apiStream(`/api/conversations/${conversationId}/messages`, {
    ...init,
    method: "POST",
    body: JSON.stringify({ content }),
  });

/**
 * `POST /api/conversations/{id}/attachments`. `apiFetch`'s `buildInit`
 * deliberately leaves `Content-Type` unset for a `FormData` body so the
 * browser sets the multipart boundary itself -- pass the `FormData` through
 * untouched. An oversized upload 413s with the server's size-limit message
 * in `detail`, which surfaces through the thrown `ApiError` like any other.
 */
export const uploadAttachment = (conversationId: string, file: File) => {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<Attachment>(`/api/conversations/${conversationId}/attachments`, {
    method: "POST",
    body: form,
  });
};
