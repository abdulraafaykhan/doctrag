import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import type { ChatEvent } from "./types";
const encoder = new TextEncoder();
function stream(text: string, width = 1) {
  const bytes = encoder.encode(text);
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (let i = 0; i < bytes.length; i += width)
        controller.enqueue(bytes.slice(i, i + width));
      controller.close();
    },
  });
}
beforeEach(() => localStorage.clear());
describe("POST event stream framing", () => {
  it("handles UTF-8, CRLF split across chunks, heartbeats and every event type", async () => {
    const received: ChatEvent[] = [];
    const text =
      ': heartbeat\r\n\r\nevent: status\r\ndata: {"text":"Thinking…"}\r\n\r\nevent: meta\r\ndata: {"session_id":"s","sources":[],\r\ndata: "account_cards":[]}\r\n\r\nevent: token\r\ndata: {"t":"INR 6,289.80 €"}\r\n\r\nevent: done\r\ndata: {"session_id":"s","cited":[]}\r\n\r\n';
    await api.readEvents(stream(text), (item) => received.push(item));
    expect(received.map((item) => item.type)).toEqual([
      "status",
      "meta",
      "token",
      "done",
    ]);
    expect(received[2]).toEqual({ type: "token", t: "INR 6,289.80 €" });
  });
  it("accepts a buffered one-token answer and terminal busy error", async () => {
    const received: ChatEvent[] = [];
    await api.readEvents(
      stream(
        'data: {"type":"token","t":"Complete answer"}\n\ndata: {"type":"error","detail":"The assistant is busy. Please try again in about 20 seconds."}\n\n',
        7,
      ),
      (item) => received.push(item),
    );
    expect(received).toHaveLength(2);
    expect(received[1].type).toBe("error");
  });
  it("reports truncated or malformed replies instead of silently succeeding", async () => {
    await expect(
      api.readEvents(
        stream('event: token\ndata: {"t":"partial"}\n\n'),
        vi.fn(),
      ),
    ).rejects.toThrow("interrupted");
    await expect(
      api.readEvents(stream("event: token\ndata: nope\n\n"), vi.fn()),
    ).rejects.toThrow("could not be read");
  });
});

describe("anonymous session HTTP contract", () => {
  it("sends the session header and omits identity from the chat body", async () => {
    api.setSession("fake-session");
    const fetch = vi
      .fn()
      .mockResolvedValue(
        new Response(
          stream(
            'event: done\ndata: {"session_id":"fake-session","cited":[]}\n\n',
          ),
          { headers: { "Content-Type": "text/event-stream" } },
        ),
      );
    vi.stubGlobal("fetch", fetch);
    await api.sendChat(
      "fake-session",
      "Hello",
      ["file-id"],
      new AbortController().signal,
      vi.fn(),
    );
    const init = fetch.mock.calls[0][1];
    expect(init.headers.get("X-Session-Id")).toBe("fake-session");
    expect(init.headers.has("Authorization")).toBe(false);
    expect(JSON.parse(init.body)).toEqual({
      message: "Hello",
      attachment_ids: ["file-id"],
      stream: true,
    });
    expect(api.getSession()).toBe("fake-session");
  });
  it("shows friendly retry timing and verification errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          new Response('{"detail":"Please try again in 12 seconds."}', {
            status: 429,
            headers: { "Retry-After": "12" },
          }),
        )
        .mockResolvedValueOnce(
          new Response(
            '{"detail":{"code":"verification_required","message":"Please verify in chat first."}}',
            { status: 403 },
          ),
        ),
    );
    await expect(api.history("s")).rejects.toMatchObject({
      status: 429,
      message: "Please try again in 12 seconds.",
      retryAt: expect.any(Number),
    });
    await expect(api.attachments("s")).rejects.toMatchObject({
      status: 403,
      message: "Please verify in chat first.",
    });
  });
});
