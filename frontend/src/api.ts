import type {
  Attachment,
  ChatEvent,
  ChatSession,
  HistoryMessage,
  UploadResult,
} from "./types";

export const SESSION_KEY = "harbourline.session";
// This random bearer id stays local to the browser and never goes into logs.
export const getSession = () => localStorage.getItem(SESSION_KEY);
export function setSession(id: string) {
  localStorage.setItem(SESSION_KEY, id);
}
export class ApiError extends Error {
  constructor(
    message: string,
    public status = 0,
    public retryAt = 0,
  ) {
    super(message);
  }
}
export const isAbort = (error: unknown) =>
  error instanceof DOMException && error.name === "AbortError";
export const friendly = (error: unknown) =>
  error instanceof ApiError
    ? error.message
    : "We could not complete that request. Please try again.";
async function request(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  const id = getSession();
  if (id && !headers.has("X-Session-Id")) headers.set("X-Session-Id", id);
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers, credentials: "omit" });
  } catch (error) {
    if (isAbort(error)) throw error;
    throw new ApiError("We could not connect. Please try again.");
  }
  if (response.ok) return response;
  let detail = "";
  try {
    const body = await response.json();
    detail =
      typeof body.detail === "string"
        ? body.detail
        : body.detail?.message || "";
  } catch {
    /* HTML and validation bodies are not safe user-facing errors. */
  }
  if (response.status === 429) {
    const value = response.headers.get("Retry-After") || "60";
    const number = Number(value);
    const seconds = Number.isFinite(number)
      ? Math.max(1, Math.ceil(number))
      : 60;
    throw new ApiError(
      detail || `Please try again in ${seconds} seconds.`,
      429,
      Date.now() + seconds * 1000,
    );
  }
  if (response.status === 404)
    throw new ApiError("This chat ended after inactivity", 404);
  if (response.status === 403)
    throw new ApiError(
      detail || "Please verify in chat before attaching files.",
      403,
    );
  if (response.status === 400 && detail) throw new ApiError(detail, 400);
  throw new ApiError(
    "We could not complete that request. Please try again.",
    response.status,
  );
}
async function json<T>(path: string, init?: RequestInit): Promise<T> {
  return (await request(path, init)).json() as Promise<T>;
}
const sessionPath = (id: string) => `/sessions/${encodeURIComponent(id)}`;
export const createSession = (signal?: AbortSignal) =>
  json<ChatSession>("/sessions", { method: "POST", signal });
export const history = (id: string, signal?: AbortSignal) =>
  json<HistoryMessage[]>(sessionPath(id) + "/history", { signal });
export const attachments = (id: string, signal?: AbortSignal) =>
  json<Attachment[]>(sessionPath(id) + "/attachments", { signal });
export const deleteSession = (id: string, signal?: AbortSignal) =>
  json(sessionPath(id), { method: "DELETE", signal });
export async function upload(
  id: string,
  files: File[],
  signal?: AbortSignal,
): Promise<UploadResult[]> {
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  return json<UploadResult[]>(sessionPath(id) + "/attachments", {
    method: "POST",
    body: form,
    signal,
  });
}

// SSE framing is independent of fetch chunks: CRLF, multiline data and UTF-8 may split anywhere.
export async function readEvents(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: ChatEvent) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "",
    eventName = "",
    data: string[] = [],
    terminal = false;
  const dispatch = () => {
    if (!data.length) {
      eventName = "";
      return;
    }
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(data.join("\n"));
    } catch {
      throw new ApiError("The reply could not be read. Please try again.");
    }
    const kind = eventName || payload.type;
    if (["status", "meta", "token", "done", "error"].includes(String(kind))) {
      if (kind === "token" && typeof payload.t !== "string")
        throw new ApiError("The reply could not be read. Please try again.");
      onEvent({ ...payload, type: kind } as ChatEvent);
      if (kind === "done" || kind === "error") terminal = true;
    }
    eventName = "";
    data = [];
  };
  const line = (value: string) => {
    if (!value) {
      dispatch();
      return;
    }
    if (value.startsWith(":")) return;
    const split = value.indexOf(":");
    const field = split < 0 ? value : value.slice(0, split);
    let content = split < 0 ? "" : value.slice(split + 1);
    if (content.startsWith(" ")) content = content.slice(1);
    if (field === "event") eventName = content;
    if (field === "data") data.push(content);
  };
  const drain = (eof = false) => {
    let match: RegExpExecArray | null;
    while ((match = /[\r\n]/.exec(buffer))) {
      const index = match.index;
      if (buffer[index] === "\r" && index === buffer.length - 1 && !eof) break;
      const width =
        buffer[index] === "\r" && buffer[index + 1] === "\n" ? 2 : 1;
      line(buffer.slice(0, index));
      buffer = buffer.slice(index + width);
      if (terminal) break;
    }
  };
  try {
    while (!terminal) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      drain(done);
      if (done) {
        if (buffer) line(buffer);
        if (!terminal) dispatch();
        break;
      }
    }
    if (!terminal)
      throw new ApiError("The reply was interrupted. Please try again.");
  } finally {
    try {
      await reader.cancel();
    } finally {
      reader.releaseLock();
    }
  }
}
export async function sendChat(
  id: string,
  message: string,
  ids: string[],
  signal: AbortSignal,
  onEvent: (event: ChatEvent) => void,
) {
  const response = await request("/chat", {
    method: "POST",
    signal,
    headers: {
      "Content-Type": "application/json",
      "X-Session-Id": id,
      Accept: "text/event-stream",
    },
    body: JSON.stringify({
      message,
      attachment_ids: ids,
      stream: true,
    }),
  });
  if (
    !response.headers.get("content-type")?.includes("text/event-stream") ||
    !response.body
  ) {
    throw new ApiError(
      "The server did not return a streaming reply. Please try again.",
    );
  }
  await readEvents(response.body, onEvent);
}
