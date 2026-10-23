import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import App from "./App";
import { LEASE_KEY, freshLease } from "./useTabLease";
import * as api from "./api";

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("BroadcastChannel", undefined);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/sessions")
        return new Response('{"session_id":"demo-session"}', { status: 201 });
      if (url.endsWith("/history")) return new Response("[]");
      if (url.endsWith("/attachments"))
        return new Response(
          '{"detail":{"code":"verification_required","message":"Please verify first."}}',
          { status: 403 },
        );
      return new Response("{}", { status: 404 });
    }),
  );
});

it("opens one anonymous chat with no sign-in screen or sidebar", async () => {
  render(<App />);
  expect(
    await screen.findByText("Harbourline Bank Assistant"),
  ).toBeInTheDocument();
  await waitFor(() => expect(screen.getByLabelText("Message")).toBeEnabled());
  expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
  expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  expect(localStorage.getItem(api.SESSION_KEY)).toBe("demo-session");
});
it("blocks a fresh second tab without making any API request", () => {
  localStorage.setItem(
    LEASE_KEY,
    JSON.stringify({ tabId: "other", timestamp: Date.now() }),
  );
  render(<App />);
  expect(
    screen.getByText("This chat is already open in another tab."),
  ).toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});
it("ignores a stale lease and recovers when the owning tab releases", async () => {
  localStorage.setItem(
    LEASE_KEY,
    JSON.stringify({ tabId: "stale", timestamp: Date.now() - 31_000 }),
  );
  render(<App />);
  expect(
    await screen.findByText("Harbourline Bank Assistant"),
  ).toBeInTheDocument();
  expect(
    freshLease(
      JSON.stringify({ tabId: "x", timestamp: Date.now() - 30_000 }),
      Date.now(),
    ),
  ).toBeNull();
});
it("a blocked tab recovers from a storage release event", async () => {
  localStorage.setItem(
    LEASE_KEY,
    JSON.stringify({ tabId: "other", timestamp: Date.now() }),
  );
  render(<App />);
  localStorage.removeItem(LEASE_KEY);
  fireEvent(
    window,
    new StorageEvent("storage", { key: LEASE_KEY, newValue: null }),
  );
  expect(
    await screen.findByText("Harbourline Bank Assistant"),
  ).toBeInTheDocument();
});
it("handles expired sessions by starting fresh and never resending input", async () => {
  localStorage.setItem(api.SESSION_KEY, "expired");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url === "/sessions"
        ? new Response('{"session_id":"new-session"}', { status: 201 })
        : new Response('{"detail":"Session not found"}', { status: 404 }),
    ),
  );
  render(<App />);
  expect(
    await screen.findByText("This chat ended after inactivity"),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(localStorage.getItem(api.SESSION_KEY)).toBe("new-session"),
  );
});
it("clears the old conversation and files on customer_changed metadata", async () => {
  localStorage.setItem(api.SESSION_KEY, "demo-session");
  let calls = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/chat")
        return new Response(
          'event: meta\ndata: {"sources":[],"account_cards":[],"clear_history":true}\n\nevent: token\ndata: {"t":"Earlier context cleared"}\n\nevent: done\ndata: {"session_id":"demo-session","cited":[]}\n\n',
          { headers: { "Content-Type": "text/event-stream" } },
        );
      if (url.endsWith("/history"))
        return new Response(
          JSON.stringify(
            calls++
              ? [
                  {
                    role: "assistant",
                    content: "Earlier context cleared",
                    sources: [],
                    attachments: [],
                  },
                ]
              : [
                  {
                    role: "assistant",
                    content: "Old customer answer",
                    sources: [],
                    attachments: [],
                  },
                ],
          ),
        );
      return new Response("[]");
    }),
  );
  render(<App />);
  expect(await screen.findByText("Old customer answer")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Message"), {
    target: { value: "123456" },
  });
  fireEvent.click(screen.getByLabelText("Send message"));
  expect(
    await screen.findByText("Earlier context cleared"),
  ).toBeInTheDocument();
  expect(screen.queryByText("Old customer answer")).not.toBeInTheDocument();
  expect(screen.queryByText("123456")).not.toBeInTheDocument();
});
