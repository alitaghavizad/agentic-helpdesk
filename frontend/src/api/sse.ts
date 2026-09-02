/**
 * Reads a text/event-stream response body.
 *
 * Not EventSource: every backend route authenticates from an Authorization
 * header and EventSource cannot send one. Passing the token as a query
 * parameter instead would write JWTs into access logs and Referer headers,
 * so all three streams -- notifications, admin runs, and a chat turn (which
 * is SSE over POST and could never have been EventSource anyway) -- read
 * their bodies here.
 *
 * Frame splitting only. Reconnection, backoff and abort belong to the
 * caller: the three streams want three different policies, and a chat turn
 * must never be retried at all.
 */
export async function* readSse(
  response: Response,
): AsyncGenerator<Record<string, unknown>> {
  if (!response.body) return;
  const reader = response.body.getReader();
  // stream: true so a multi-byte character split across two chunks is held
  // until its remaining bytes arrive rather than decoded as replacement
  // characters.
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      for (;;) {
        const boundary = buffer.indexOf("\n\n");
        if (boundary === -1) break;
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trim())
          .join("\n");
        // Empty means a comment-only frame -- the backend's ": keepalive".
        if (data) yield JSON.parse(data) as Record<string, unknown>;
      }
    }
    // Whatever is left in `buffer` is a frame the stream was cut in the
    // middle of. Dropping it is correct: half a JSON object is not an
    // event, and throwing here would turn a dropped connection into an
    // unhandled rejection in whichever component was listening.
  } finally {
    reader.releaseLock();
  }
}
