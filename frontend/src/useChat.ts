import { useEffect, useRef, useState } from "react";
import type { FormEvent, ChangeEvent } from "react";
import * as api from "./api";
import type { Message, Source, FileChip, HistoryMessage } from "./types";
import { validateFiles } from "./limits";

const key = () => crypto.randomUUID();
const restore = (rows: HistoryMessage[]): Message[] =>
  rows.map((row) => ({
    key: key(),
    role: row.role,
    content: row.content,
    sources: row.sources || [],
    cards: row.account_cards || [],
    attachments: row.attachments || [],
    notes: [],
  }));

// Keep anonymous session/network state apart from the conversation's presentation.
export function useChat() {
  const [id, setId] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [files, setFiles] = useState<FileChip[]>([]);
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [retryAt, setRetryAt] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [source, setSource] = useState<Source | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const seconds = Math.max(0, Math.ceil((retryAt - now) / 1000));

  const fresh = async (signal?: AbortSignal) => {
    const row = await api.createSession(signal);
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    api.setSession(row.session_id);
    setId(row.session_id);
    setMessages([]);
    setFiles([]);
    setSource(null);
    return row.session_id;
  };
  const problem = async (error: unknown) => {
    if (api.isAbort(error)) return;
    setNotice(api.friendly(error));
    if (error instanceof api.ApiError && error.status === 429)
      setRetryAt(error.retryAt);
    if (error instanceof api.ApiError && error.status === 403) setFiles([]);
    if (error instanceof api.ApiError && error.status === 404) {
      // Never resend an expired turn automatically: it might contain a verification credential.
      setDraft("");
      try {
        await fresh(controller.current?.signal);
      } catch (next) {
        if (!api.isAbort(next)) setNotice(api.friendly(next));
      }
    }
  };
  useEffect(() => {
    const abort = new AbortController();
    controller.current = abort;
    void (async () => {
      try {
        const saved = api.getSession();
        if (!saved) {
          await fresh(abort.signal);
          return;
        }
        const rows = await api.history(saved, abort.signal);
        setId(saved);
        setMessages(restore(rows));
        try {
          const attached = await api.attachments(saved, abort.signal);
          setFiles(
            attached.map((row) => ({
              key: row.id,
              id: row.id,
              filename: row.filename,
              status: row.status,
            })),
          );
        } catch (error) {
          if (!(error instanceof api.ApiError && error.status === 403))
            throw error;
        }
      } catch (error) {
        await problem(error);
      }
    })();
    return () => abort.abort();
  }, []);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!id || !files.some((file) => file.status === "processing")) return;
    const abort = new AbortController();
    const timer = window.setInterval(() => {
      void api
        .attachments(id, abort.signal)
        .then((rows) =>
          setFiles((current) =>
            current.map((file) => {
              const row = rows.find((item) => item.id === file.id);
              return row ? { ...file, status: row.status } : file;
            }),
          ),
        )
        .catch((error) => {
          if (!api.isAbort(error)) {
            setFiles([]);
            void problem(error);
          }
        });
    }, 1500);
    return () => {
      window.clearInterval(timer);
      abort.abort();
    };
  }, [id, files.some((file) => file.status === "processing")]);

  const newChat = async () => {
    if (busy || files.some((file) => file.status === "uploading")) return;
    setBusy(true);
    setNotice("");
    setDraft("");
    try {
      if (id) {
        try {
          await api.deleteSession(id);
        } catch (error) {
          if (!(error instanceof api.ApiError && error.status === 404))
            throw error;
        }
      }
      await fresh();
    } catch (error) {
      await problem(error);
    } finally {
      setBusy(false);
    }
  };
  const attach = async (event: ChangeEvent<HTMLInputElement>) => {
    const chosen = Array.from(event.target.files || []);
    event.target.value = "";
    if (!chosen.length || !id || busy) return;
    const issue = validateFiles(chosen, files);
    if (issue) {
      setNotice(issue);
      return;
    }
    const chips: FileChip[] = chosen.map((file) => ({
      key: key(),
      filename: file.name,
      status: "uploading",
    }));
    setFiles((current) => [...current, ...chips]);
    try {
      const abort = new AbortController();
      controller.current = abort;
      const rows = await api.upload(id, chosen, abort.signal);
      setFiles((current) =>
        current.map((file) => {
          const index = chips.findIndex((chip) => chip.key === file.key);
          const result = rows[index];
          return index >= 0 && result
            ? {
                ...file,
                filename: result.filename,
                id: result.doc_id,
                status: result.status,
                error: result.error,
              }
            : file;
        }),
      );
    } catch (error) {
      setFiles((current) =>
        current.filter((file) => !chips.some((chip) => chip.key === file.key)),
      );
      await problem(error);
    }
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (
      !draft.trim() ||
      !id ||
      busy ||
      seconds ||
      files.some((file) => file.status === "uploading")
    )
      return;
    const message = draft.trim();
    setDraft("");
    setNotice("");
    setBusy(true);
    setStatus("Thinking...");
    const replyKey = key();
    setMessages((current) => [
      ...current,
      {
        key: replyKey,
        role: "assistant",
        content: "",
        sources: [],
        cards: [],
        attachments: [],
        notes: [],
        pending: true,
      },
    ]);
    const abort = new AbortController();
    controller.current = abort;
    const patch = (values: Partial<Message>) =>
      setMessages((current) =>
        current.map((row) =>
          row.key === replyKey ? { ...row, ...values } : row,
        ),
      );
    let answer = "",
      failed = false;
    try {
      await api.sendChat(
        id,
        message,
        files
          .filter(
            (file) => file.status === "ready" || file.status === "processing",
          )
          .flatMap((file) => (file.id ? [file.id] : [])),
        abort.signal,
        (event) => {
          if (event.type === "status") setStatus(event.text);
          if (event.type === "meta") {
            // The server clears all prior customer context; mirror that wipe immediately.
            if (event.clear_history) {
              setMessages((current) =>
                current.filter((row) => row.key === replyKey),
              );
              setFiles([]);
              setSource(null);
            }
            patch({
              sources: event.sources,
              cards: event.account_cards || [],
              notes: event.notes || [],
            });
          }
          if (event.type === "token") {
            answer += event.t;
            patch({ content: answer });
          }
          if (event.type === "done") patch({ pending: false });
          if (event.type === "error") {
            failed = true;
            patch({ pending: false, error: event.detail });
            setNotice(event.detail);
          }
        },
      );
      // Echo only server-masked history; raw verification values never become chat bubbles.
      const rows = await api.history(id, abort.signal);
      if (!failed) setMessages(restore(rows));
      // Refresh private-file visibility after verification, without counting as activity.
      try {
        const attached = await api.attachments(id, abort.signal);
        setFiles(
          attached.map((row) => ({
            key: row.id,
            id: row.id,
            filename: row.filename,
            status: row.status,
          })),
        );
      } catch (error) {
        if (error instanceof api.ApiError && error.status === 403) setFiles([]);
        else throw error;
      }
    } catch (error) {
      patch({ pending: false, error: api.friendly(error) });
      await problem(error);
    } finally {
      setBusy(false);
      setStatus("");
    }
  };
  return {
    id,
    messages,
    files,
    draft,
    notice,
    status,
    busy,
    seconds,
    source,
    setSource,
    setDraft,
    newChat,
    attach,
    submit,
    dismissFile: (key: string) =>
      setFiles((current) => current.filter((file) => file.key !== key)),
  };
}
export type ChatState = ReturnType<typeof useChat>;
