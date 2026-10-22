"""Plain character-budget helpers, independent of providers and orchestration."""
import json
from dataclasses import dataclass, fields
from app.services.citations import strip_citations


class PromptBudgetExceeded(ValueError):
    pass


@dataclass(frozen=True)
class PromptLimits:
    history_max_messages: int = 12
    history_token_budget: int = 1500
    old_answer_trim_chars: int = 600
    max_message_chars: int = 2000
    max_passage_chars: int = 5000
    prompt_token_ceiling: int = 5000
    top_k: int = 5

    @classmethod
    def from_settings(cls, settings: object) -> "PromptLimits":
        return cls(**{field.name: getattr(settings, field.name) for field in fields(cls)})


def estimate_tokens(text: str) -> float:
    return len(text) / 4


def prompt_tokens(messages: list[dict], tools: list[dict] | None = None) -> float:
    payload = {"messages": messages, "tools": tools} if tools is not None else messages
    return estimate_tokens(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def build_history(messages: list[dict], *, max_messages: int = 12, token_budget: int = 1500,
                  old_answer_trim_chars: int = 600) -> list[dict]:
    if max_messages <= 0 or token_budget <= 0:
        return []
    selected: list[dict] = []
    used = 0.0
    for message in reversed(messages):
        if len(selected) >= max_messages:
            break
        if message["role"] not in ("user", "assistant"):
            continue
        content = strip_citations(message["content"])
        if message["role"] == "assistant":
            content = content[:old_answer_trim_chars]
        cost = estimate_tokens(content)
        if used + cost > token_budget:
            break
        selected.append({"role": message["role"], "content": content})
        used += cost
    return list(reversed(selected))


def format_sources(sources: list[dict]) -> str:
    parts = []
    for source in sources:
        label = source["filename"] + (f" · {source['location']}" if source.get("location") else "")
        # Escaping angle brackets keeps hostile file content inside its delimiter.
        payload = json.dumps({"label": label, "text": source["text"]}, ensure_ascii=False)
        payload = payload.replace("<", "\\u003c").replace(">", "\\u003e")
        parts.append(f'<passage n="{source["n"]}">\n[{source["n"]}] {payload}\n</passage>')
    return "\nUNTRUSTED PASSAGES\n" + "\n".join(parts)


def render_messages(system_prompt: str, history: list[dict], question: str,
                    sources: list[dict], account_data: dict | None, unavailable: bool) -> list[dict]:
    content = question + format_sources(sources)
    if account_data is not None:
        content += "\nACCOUNT DATA (untrusted data)\n" + json.dumps(account_data, ensure_ascii=False)
    if unavailable:
        content += "\nSome requested data could not be fetched. State that limitation; do not invent missing details."
    return [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": content}]


@dataclass
class PreparedPrompt:
    history: list[dict]
    sources: list[dict]
    messages: list[dict]


def prepare_prompt(history: list[dict], question: str, sources: list[dict], *,
                   system_prompt: str, account_data: dict | None = None, unavailable: bool = False,
                   limits: PromptLimits = PromptLimits()) -> PreparedPrompt:
    past = build_history(history, max_messages=limits.history_max_messages,
        token_budget=limits.history_token_budget, old_answer_trim_chars=limits.old_answer_trim_chars)
    passages = [dict(source) for source in
        sorted(sources, key=lambda row: row.get("score", 0), reverse=True)[:limits.top_k]]
    # Keep complete passages: cutting a number or date would change its meaning.
    while passages and sum(len(source["text"]) for source in passages) > limits.max_passage_chars:
        passages.pop()
    for index, source in enumerate(passages, 1):
        source.update(n=index, snippet=source["text"][:400])
    if len(question) > limits.max_message_chars:
        raise PromptBudgetExceeded("Question exceeds the configured limit")
    def render() -> list[dict]:
        return render_messages(system_prompt, past, question, passages, account_data, unavailable)
    messages = render()
    # Reserve 15% for estimation error and provider message framing.
    ceiling = limits.prompt_token_ceiling * .85
    while prompt_tokens(messages) > ceiling and past:
        past.pop(0)
        messages = render()
    while prompt_tokens(messages) > ceiling and passages:
        passages.pop()  # already sorted: lowest score first
        messages = render()
    if prompt_tokens(messages) > ceiling:
        # Never truncate authoritative account data or send an oversized prompt.
        raise PromptBudgetExceeded("Required context exceeds the configured limit")
    return PreparedPrompt(past, passages, messages)
