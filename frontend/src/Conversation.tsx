import { useEffect, useRef } from "react";
import { AccountCards } from "./AccountCards";
import { Answer } from "./Answer";
import type { ChatState } from "./useChat";

// Safe Markdown and account cards share the same server-approved conversation.
export function Conversation({ chat }: { chat: ChatState }) {
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottom.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [chat.messages, chat.status]);
  return (
    <main className="conversation" aria-label="Conversation">
      {!chat.messages.length && (
        <section className="welcome">
          <span className="eyebrow">HOW CAN WE HELP?</span>
          <h1>Your bank, in plain language.</h1>
          <p>
            Ask about products, fees or your account. We'll verify your details
            in chat when needed.
          </p>
        </section>
      )}
      {chat.messages.map((message) => (
        <article key={message.key} className={`message ${message.role}`}>
          <span className="eyebrow">
            {message.role === "user" ? "YOU" : "HARBOURLINE"}
          </span>
          <AccountCards cards={message.cards} />
          <Answer
            text={message.content}
            sources={message.sources}
            onSource={chat.setSource}
          />
          {message.notes.map((note) => (
            <p className="muted" key={note}>
              {note}
            </p>
          ))}
          {message.pending && (
            <p className="muted" role="status">
              {chat.status || "Thinking..."}
            </p>
          )}
          {message.error && <p role="alert">{message.error}</p>}
        </article>
      ))}
      <div ref={bottom} />
    </main>
  );
}
