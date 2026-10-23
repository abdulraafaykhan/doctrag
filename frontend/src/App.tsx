import { useTabLease } from "./useTabLease";
import { useChat } from "./useChat";
import { Conversation } from "./Conversation";
import { Composer } from "./Composer";
import { SourceDialog } from "./Answer";
import { Icon } from "./Icons";

export default function App() {
  const lease = useTabLease();
  if (lease === "blocked")
    return (
      <main className="full-page">
        <p>This chat is already open in another tab.</p>
      </main>
    );
  if (lease === "unavailable")
    return (
      <main className="full-page">
        <p>Please allow browser storage to open this chat.</p>
      </main>
    );
  return lease === "active" ? (
    <Chat />
  ) : (
    <main className="full-page">Opening chat...</main>
  );
}

function Chat() {
  const chat = useChat();
  return (
    <div className="chat-shell">
      <header className="app-header">
        <div>
          <strong>Harbourline Bank Assistant</strong>
          <small>Fictional bank | demo data only</small>
        </div>
        <button
          onClick={() => void chat.newChat()}
          disabled={
            chat.busy ||
            !chat.id ||
            chat.files.some((file) => file.status === "uploading")
          }
        >
          <Icon name="plus" />
          New chat
        </button>
      </header>
      <Conversation chat={chat} />
      <Composer chat={chat} />
      <SourceDialog source={chat.source} onClose={() => chat.setSource(null)} />
    </div>
  );
}
