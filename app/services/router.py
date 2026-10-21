"""Decide, per message, what the assistant needs: the user's documents, the web, both, or neither.

Instead of always running a vector search, a small fast model reads the message (plus the conversation and the
names of the uploaded files) and returns a plan. Plain questions, chit-chat, coding help, brainstorming and
general knowledge then go straight to the main model, which answers with its full intelligence.
"""
import json
import re
from dataclasses import dataclass, field

from app.config import get_settings
from app.services.llm import client
from app.services.web import extract_urls

PLANNER_PROMPT = """You are the planning step of an AI assistant. Decide what information is needed to answer the user's latest message.
Return ONLY a JSON object with these keys:
- "standalone_question": the latest message rewritten so it makes sense alone (resolve "it", "that", "the second one" using the conversation). Keep the user's language.
- "use_documents": true if the message is about, or could be answered from, the user's uploaded files listed below (summaries, "the document", "my report", questions on their specific content). false for general knowledge, chit-chat, writing, coding, maths, brainstorming, or when no files are listed.
- "search_web": true if it needs live or outside information: it contains a URL or website name, asks about a specific company/product/person/page/news, asks for current facts (prices, versions, events, "latest", "today"), or the user asks to search/browse/look something up. false when your own knowledge is enough.
- "web_query": a short search-engine query for the web lookup (empty string if search_web is false). For a bare website or company name use e.g. "Stripe company official website".
- "urls": URLs the user is referring to (from the message or the conversation); [] if none.
Be conservative: do not search the web for things a knowledgeable assistant already knows."""


@dataclass
class Plan:
    """What the planner thinks the message needs (`wants_*`) and what will actually be done (the rest).

    The two differ because of the user's web switch: with web 'off' nothing is fetched even if the message needs
    the web, and `wants_web` is how the chat flow knows to answer "turn on web search" instead.
    """
    question: str
    use_documents: bool = False   # effective: search the uploaded documents
    search_web: bool = False      # effective: read/search the web
    web_query: str = ""
    urls: list[str] = field(default_factory=list)
    wants_docs: bool = False      # planner's own opinion, before the web switch is applied
    wants_web: bool = False
    mode: str = "auto"


_BROWSE_WORDS = re.compile(r"\b(search (the )?(web|internet|online)|google|look (it |this |that )?up|browse|latest news|crawl|scrape)\b", re.I)


def apply_mode(plan: Plan, mode: str, doc_names: list[str]) -> Plan:
    """Turn the planner's opinion into actions according to the user's web switch.

    off  : pure document RAG. Documents are always searched (when there are any); the web never is.
    on   : the web is always used (documents too, when the planner finds them relevant).
    auto : the planner decides both.
    """
    web_ok = get_settings().web_enabled
    plan.mode = mode
    if mode == "off" or not web_ok:
        plan.use_documents = bool(doc_names) if mode == "off" else plan.wants_docs
        plan.search_web, plan.urls, plan.web_query = False, [], ""
    elif mode == "on":
        plan.use_documents = plan.wants_docs
        plan.search_web = True
        if not plan.urls and not plan.web_query:
            plan.web_query = plan.question
    else:  # auto
        plan.use_documents = plan.wants_docs
        plan.search_web = plan.wants_web
        if not plan.search_web:
            plan.urls, plan.web_query = [], ""
        elif not plan.urls and not plan.web_query:
            plan.web_query = plan.question
    return plan


async def plan_turn(history: list[dict], message: str, doc_names: list[str], web_mode: str = "auto") -> Plan:
    """web_mode: 'auto' (planner decides), 'on' (always look on the web), 'off' (documents only)."""
    s = get_settings()
    typed_urls = extract_urls(message)
    known_text = message + "\n" + "\n".join(m["content"] for m in history if m["role"] == "user")

    # Sensible plan if the planner model is unavailable.
    plan = Plan(
        question=message,
        wants_docs=bool(doc_names),
        wants_web=bool(typed_urls) or bool(_BROWSE_WORDS.search(message)),
        web_query=message if _BROWSE_WORDS.search(message) else "",
        urls=typed_urls,
    )

    transcript = "\n".join(f"{m['role']}: {m['content'][:400]}" for m in history[-4:]) or "(none)"
    files = ", ".join(doc_names[:15]) or "(none uploaded)"
    try:
        resp = await client().chat.completions.create(
            model=s.groq_rewrite_model,
            messages=[
                {"role": "system", "content": PLANNER_PROMPT},
                {"role": "user", "content": f"Uploaded files: {files}\n\nConversation so far:\n{transcript}\n\nLatest message: {message}"},
            ],
            temperature=0,
            max_tokens=250,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception:  # noqa: BLE001 - planning is an optimisation; the fallback plan above still works
        return apply_mode(plan, web_mode, doc_names)

    plan.question = (data.get("standalone_question") or message).strip() or message
    plan.wants_docs = bool(doc_names) and bool(data.get("use_documents"))
    plan.wants_web = bool(data.get("search_web")) or bool(typed_urls)
    # URLs: trust only ones that really appear in the conversation, never ones the planner invented.
    said = [u for u in (data.get("urls") or []) if isinstance(u, str) and u.rstrip("/") in known_text]
    plan.urls = list(dict.fromkeys(typed_urls + said))[:3]
    plan.web_query = (data.get("web_query") or "").strip()
    return apply_mode(plan, web_mode, doc_names)