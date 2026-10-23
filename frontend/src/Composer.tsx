import { useRef } from "react";
import { Icon } from "./Icons";
import { ACCEPT, MAX_MESSAGE_CHARS, MAX_UPLOAD_MB } from "./limits";
import type { ChatState } from "./useChat";

// Files upload before the turn; ready ids are passed by the request hook.
export function Composer({ chat }: { chat: ChatState }) {
  const fileInput = useRef<HTMLInputElement>(null);
  return (
    <footer className="composer-area">
      {chat.notice && (
        <div className="notice" role="alert">
          {chat.notice}
        </div>
      )}
      <div className="file-chips">
        {chat.files.map((file) => (
          <span className={`file-chip ${file.status}`} key={file.key}>
            {file.filename} | {file.status}
            {file.error && <small>{file.error}</small>}
            {!file.id && file.status !== "uploading" && (
              <button
                aria-label={`Dismiss ${file.filename}`}
                onClick={() => chat.dismissFile(file.key)}
              >
                x
              </button>
            )}
          </span>
        ))}
      </div>
      <form className="composer" onSubmit={(event) => void chat.submit(event)}>
        <input
          type="file"
          ref={fileInput}
          multiple
          accept={ACCEPT}
          hidden
          onChange={(event) => void chat.attach(event)}
        />
        <button
          type="button"
          aria-label="Attach files"
          disabled={chat.busy || !chat.id}
          onClick={() => fileInput.current?.click()}
        >
          <Icon name="clip" />
        </button>
        <textarea
          aria-label="Message"
          placeholder="Ask a banking question..."
          value={chat.draft}
          onChange={(event) => chat.setDraft(event.target.value)}
          maxLength={MAX_MESSAGE_CHARS}
          disabled={chat.busy || !chat.id}
          rows={2}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
        />
        <button
          type="submit"
          aria-label="Send message"
          disabled={
            chat.busy || !chat.id || !chat.draft.trim() || chat.seconds > 0
          }
        >
          <Icon name="send" />
        </button>
      </form>
      <p className="fine-print">
        {chat.seconds
          ? `Please try again in ${chat.seconds} seconds.`
          : `Up to 3 files per upload, 5 per chat | ${MAX_UPLOAD_MB} MB each | Verify in chat before uploading`}
      </p>
    </footer>
  );
}
