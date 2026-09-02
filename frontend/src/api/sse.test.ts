import { describe, expect, it } from "vitest";
import { readSse } from "./sse";

function responseOf(...chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body);
}

async function collect(response: Response) {
  const out: unknown[] = [];
  for await (const frame of readSse(response)) out.push(frame);
  return out;
}

describe("readSse", () => {
  it("yields one object per frame", async () => {
    const frames = await collect(
      responseOf('data: {"type":"token","text":"a"}\n\ndata: {"type":"done"}\n\n'),
    );
    expect(frames).toEqual([{ type: "token", text: "a" }, { type: "done" }]);
  });

  it("ignores the backend's keepalive comment frames", async () => {
    // The backend sends ": keepalive\n\n" every 15s on both streams.
    const frames = await collect(responseOf(': keepalive\n\ndata: {"type":"done"}\n\n'));
    expect(frames).toEqual([{ type: "done" }]);
  });

  it("reassembles a frame split across two network chunks", async () => {
    const frames = await collect(responseOf('data: {"type":"tok', 'en","text":"x"}\n\n'));
    expect(frames).toEqual([{ type: "token", text: "x" }]);
  });

  it("reassembles a frame whose delimiter is split across chunks", async () => {
    const frames = await collect(responseOf('data: {"a":1}\n', '\ndata: {"a":2}\n\n'));
    expect(frames).toEqual([{ a: 1 }, { a: 2 }]);
  });

  it("drops a truncated final frame rather than throwing", async () => {
    // A stream cut mid-frame must not take the page down with a JSON error.
    const frames = await collect(responseOf('data: {"a":1}\n\ndata: {"a":'));
    expect(frames).toEqual([{ a: 1 }]);
  });

  it("handles a multi-byte character split across chunks", async () => {
    const encoder = new TextEncoder();
    const bytes = encoder.encode('data: {"t":"café"}\n\n');
    const split = 14; // lands inside the é
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, split));
        controller.enqueue(bytes.slice(split));
        controller.close();
      },
    });
    expect(await collect(new Response(body))).toEqual([{ t: "café" }]);
  });
});
