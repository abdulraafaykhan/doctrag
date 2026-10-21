"""Terminal chat client for testing multi-turn conversations against the running API.

1) Start the server:   uv run uvicorn app.main:app --reload
2) In another terminal: uv run python chat_cli.py
Type 'exit' to quit, 'new' to start a fresh conversation.
"""
import json

import httpx

BASE = "http://127.0.0.1:8000"


def main() -> None:
    session_id = None
    print("doctrag chat. Type 'exit' to quit, 'new' for a new conversation.")
    while True:
        question = input("\nYou: ").strip()
        if not question:
            continue
        if question.lower() in {"exit", "quit"}:
            break
        if question.lower() == "new":
            session_id = None
            print("(new conversation)")
            continue

        payload = {"message": question, "session_id": session_id}
        sources = []
        with httpx.stream("POST", f"{BASE}/chat", json=payload, timeout=120) as r:
            if r.status_code != 200:
                r.read()
                print(f"Error {r.status_code}: {r.text}")
                continue
            print("Assistant: ", end="", flush=True)
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                ev = json.loads(line[5:].strip())
                if ev["type"] == "meta":
                    session_id = ev["session_id"]
                    sources = ev["sources"]
                    if ev["search_query"] != question:
                        print(f"\n  (searched for: {ev['search_query']})\nAssistant: ", end="", flush=True)
                elif ev["type"] == "token":
                    print(ev["t"], end="", flush=True)
                elif ev["type"] == "error":
                    print(f"\n[error] {ev['detail']}")
        if sources:
            cited = ", ".join(
                f"[{s['n']}] {s['filename']}" + (f" p.{s['page']}" if s["page"] else "") for s in sources
            )
            print(f"\n  Sources: {cited}")


if __name__ == "__main__":
    main()