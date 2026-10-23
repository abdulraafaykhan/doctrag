import { useEffect, useState } from "react";
export const LEASE_KEY = "harbourline.tab";
const FRESH_MS = 30_000;
export interface Lease {
  tabId: string;
  timestamp: number;
}
export function freshLease(value: string | null, now: number): Lease | null {
  try {
    const row = JSON.parse(value || "null");
    return typeof row?.tabId === "string" &&
      typeof row.timestamp === "number" &&
      row.timestamp <= now &&
      now - row.timestamp < FRESH_MS
      ? row
      : null;
  } catch {
    return null;
  }
}

// A browser-only lease prevents parallel tabs without extending server activity.
export function useTabLease() {
  const [tabId] = useState(() => crypto.randomUUID());
  const [state, setState] = useState<
    "checking" | "active" | "blocked" | "unavailable"
  >("checking");
  useEffect(() => {
    const channel =
      typeof BroadcastChannel !== "undefined"
        ? new BroadcastChannel(LEASE_KEY)
        : null;
    let owned = false;
    const check = () => {
      try {
        const lease = freshLease(localStorage.getItem(LEASE_KEY), Date.now());
        if (lease && lease.tabId !== tabId) {
          owned = false;
          setState("blocked");
          return;
        }
        localStorage.setItem(
          LEASE_KEY,
          JSON.stringify({ tabId, timestamp: Date.now() }),
        );
        owned = true;
        setState("active");
      } catch {
        owned = false;
        setState("unavailable");
      }
    };
    const release = () => {
      try {
        if (
          owned &&
          JSON.parse(localStorage.getItem(LEASE_KEY) || "null")?.tabId === tabId
        )
          localStorage.removeItem(LEASE_KEY);
      } catch {
        // Storage can become unavailable while a page is closing.
      } finally {
        owned = false;
        channel?.postMessage("released");
      }
    };
    const changed = (event: StorageEvent) => {
      if (event.key === LEASE_KEY) check();
    };
    check();
    const timer = window.setInterval(check, 10_000);
    window.addEventListener("storage", changed);
    window.addEventListener("pagehide", release);
    if (channel) channel.onmessage = check;
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("storage", changed);
      window.removeEventListener("pagehide", release);
      release();
      channel?.close();
    };
  }, [tabId]);
  return state;
}
