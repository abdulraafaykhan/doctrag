"""Web access: read a URL, or search the web by name/topic and read the best pages.

Free by design:
* pages are fetched with httpx and turned into clean text with trafilatura (main article text, no menus/ads)
* "by name" lookups use the `ddgs` package (DuckDuckGo, no API key). If TAVILY_API_KEY is set, Tavily is used
  instead (free tier, more reliable).

Safety: the server fetches whatever a user (or a web search) points it at, so every request - and every redirect
hop - is checked against private/internal addresses (SSRF). Responses are size-capped and time-limited.
Fetched text is passed to the model as untrusted data, never as instructions.
"""
import asyncio
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

from app.config import get_settings

log = logging.getLogger("doctrag.web")

USER_AGENT = "Mozilla/5.0 (compatible; DocTragBot/1.0; +https://example.invalid/bot)"
ALLOWED_PORTS = {None, 80, 443, 8080, 8443}
MAX_REDIRECTS = 4
MAX_PAGE_CHARS = 40_000       # text kept per page before ranking
THIN_PAGE_CHARS = 400         # below this a page is probably JavaScript-rendered or blocked

_TLDS = (
    "com|org|net|io|ai|dev|app|co|edu|gov|info|me|tv|xyz|tech|in|uk|us|ca|de|fr|jp|cn|au|ru|br|it|es|nl|se|ch|eu|"
    "gg|so|sh|cloud|blog|news|wiki|store|online|site|page"
)
_URL_RE = re.compile(r"https?://[^\s<>\"'`)\]}]+", re.I)
_BARE_RE = re.compile(rf"(?<![@\w/.:-])((?:[A-Za-z0-9-]+\.)+(?-i:{_TLDS}))(?![\w@-])(/[^\s<>\"'`)\]}}]*)?")
_TRAILING = ".,;:!?'\""


@dataclass
class Page:
    url: str
    title: str
    text: str
    note: str = ""            # e.g. "page looks empty (needs JavaScript?)"
    via: str = "url"          # "url" (given by the user) or "search"


@dataclass
class WebResult:
    pages: list[Page] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # problems worth telling the user/model about


# ------------------------------------------------------------------ URL helpers
def extract_urls(text: str, limit: int = 3) -> list[str]:
    """Find URLs in free text: full links and bare domains like 'stripe.com/pricing'."""
    found: list[str] = []
    spans: list[tuple[int, int]] = []
    for m in _URL_RE.finditer(text):
        found.append(m.group(0).rstrip(_TRAILING)); spans.append(m.span())
    for m in _BARE_RE.finditer(text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        found.append("https://" + (m.group(1) + (m.group(2) or "")).rstrip(_TRAILING))
    out: list[str] = []
    for u in found:
        u = u.strip()
        if u and u not in out:
            out.append(u)
    return out[:limit]


def _blocked_ip(ip: str) -> bool:
    a = ipaddress.ip_address(ip.split("%")[0])
    return (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved
            or a.is_unspecified or (a.version == 6 and a.ipv4_mapped is not None and _blocked_ip(str(a.ipv4_mapped))))


async def check_public_url(url: str) -> str | None:
    """Return None if the URL is safe to fetch, otherwise a short reason."""
    try:
        p = urlparse(url)
    except ValueError:
        return "not a valid URL"
    if p.scheme not in ("http", "https") or not p.hostname:
        return "only http(s) links can be opened"
    if p.username or p.password:
        return "links with embedded credentials are not allowed"
    if p.port not in ALLOWED_PORTS:
        return "unusual port"
    host = p.hostname
    try:
        ips = [host] if _is_ip(host) else [
            i[4][0] for i in await asyncio.to_thread(socket.getaddrinfo, host, p.port or 443, proto=socket.IPPROTO_TCP)
        ]
    except OSError:
        return "the address could not be resolved"
    if not ips or any(_blocked_ip(ip) for ip in ips):
        return "that address is not a public website"
    return None


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


# ------------------------------------------------------------------ fetching
async def _download(client: httpx.AsyncClient, url: str) -> tuple[str, bytes, str]:
    """GET with manual redirects (each hop re-checked). Returns (final_url, body, content_type)."""
    s = get_settings()
    for _ in range(MAX_REDIRECTS + 1):
        reason = await check_public_url(url)
        if reason:
            raise ValueError(reason)
        async with client.stream("GET", url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.5"}) as r:
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                continue
            if r.status_code >= 400:
                raise ValueError(f"the site answered HTTP {r.status_code}")
            ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
            cap = s.web_max_bytes
            body = bytearray()
            async for chunk in r.aiter_bytes():
                body += chunk
                if len(body) >= cap:
                    break  # keep what we have; very large pages are truncated, not rejected
            return url, bytes(body), ctype
    raise ValueError("too many redirects")


def _html_to_text(html: str, url: str) -> tuple[str, str]:
    """Return (title, main text)."""
    import trafilatura

    text = trafilatura.extract(html, url=url, include_tables=True, include_links=False, favor_recall=True,
                               output_format="txt") or ""
    title = ""
    try:
        meta = trafilatura.extract_metadata(html, default_url=url)
        title = (meta.title or "").strip() if meta else ""
    except Exception:  # noqa: BLE001
        pass
    if len(text) < THIN_PAGE_CHARS:  # extractor was too strict (landing pages, product pages): fall back
        try:
            import lxml.html

            doc = lxml.html.fromstring(html)
            for bad in doc.xpath("//script|//style|//noscript|//nav|//footer|//header|//form|//svg"):
                bad.drop_tree()
            if not title:
                title = (doc.findtext(".//title") or "").strip()
            raw = " ".join(doc.text_content().split())
            if len(raw) > len(text):
                text = raw
        except Exception:  # noqa: BLE001
            pass
    return title, re.sub(r"[ \t]+\n", "\n", text).strip()


async def fetch_page(client: httpx.AsyncClient, url: str, via: str = "url") -> Page:
    final, body, ctype = await _download(client, url)
    if ctype == "application/pdf" or final.lower().split("?")[0].endswith(".pdf"):
        from app.services.loaders import load_file

        res = await asyncio.to_thread(load_file, "page.pdf", body)
        text = "\n\n".join(sec.text for sec in res.sections)
        title = urlparse(final).path.rsplit("/", 1)[-1] or urlparse(final).netloc
    elif ctype in ("", "text/html", "application/xhtml+xml", "text/plain", "application/xml", "text/xml"):
        html = body.decode(_charset(body, ctype), errors="replace")
        if ctype == "text/plain":
            title, text = urlparse(final).netloc, html
        else:
            title, text = await asyncio.to_thread(_html_to_text, html, final)
    else:
        raise ValueError(f"unsupported content type ({ctype})")
    text = text[:MAX_PAGE_CHARS]
    note = "" if len(text) >= THIN_PAGE_CHARS else "very little text was readable (the site may need JavaScript or block bots)"
    return Page(url=final, title=title or urlparse(final).netloc, text=text, note=note, via=via)


def _charset(body: bytes, ctype: str) -> str:
    m = re.search(rb"charset=[\"']?([\w-]+)", body[:3000], re.I)
    if m:
        try:
            b"".decode(m.group(1).decode())
            return m.group(1).decode()
        except LookupError:
            pass
    return "utf-8"


# ------------------------------------------------------------------ searching
async def web_search(query: str, n: int | None = None) -> list[dict]:
    """Return [{title, url, snippet}]. Never raises: a failed search just returns []."""
    s = get_settings()
    n = n or s.web_search_results
    try:
        if s.tavily_api_key:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post("https://api.tavily.com/search", json={"query": query, "max_results": n},
                                 headers={"Authorization": f"Bearer {s.tavily_api_key}"})
                r.raise_for_status()
                return [{"title": x.get("title", ""), "url": x["url"], "snippet": x.get("content", "")}
                        for x in r.json().get("results", []) if x.get("url")]

        def _ddg() -> list[dict]:
            from ddgs import DDGS

            return [{"title": x.get("title", ""), "url": x.get("href", ""), "snippet": x.get("body", "")}
                    for x in DDGS(timeout=10).text(query, max_results=n) if x.get("href")]

        return await asyncio.to_thread(_ddg)
    except Exception as e:  # noqa: BLE001 - search engines rate-limit; degrade gracefully
        log.warning("web search failed: %s", e)
        return []


# ------------------------------------------------------------------ the one entry point
async def gather_web(urls: list[str], search_query: str | None) -> WebResult:
    """Read the given URLs and/or search the web, and return readable pages.

    * URLs given: those pages are read; if there is also a search query, a site-restricted search adds a
      couple of relevant pages from the same website (a light 'crawl').
    * No URL: search by name/topic and read the best results.
    """
    s = get_settings()
    out = WebResult()
    targets: list[tuple[str, str]] = [(u, "url") for u in urls[:3]]

    if search_query:
        if urls:
            host = urlparse(urls[0]).hostname or ""
            hits = await web_search(f"site:{host} {search_query}", 4) if host else []
            extra = [h["url"] for h in hits if h["url"].rstrip("/") not in {u.rstrip("/") for u in urls}][:2]
            targets += [(u, "search") for u in extra]
        else:
            hits = await web_search(search_query)
            if not hits:
                out.notes.append("The web search returned no results (the search service may be rate-limiting).")
            targets += [(h["url"], "search") for h in hits][: s.web_max_pages]

    seen: set[str] = set()
    targets = [(u, v) for u, v in targets if not (u in seen or seen.add(u))][: max(s.web_max_pages, len(urls[:3]))]

    async with httpx.AsyncClient(timeout=s.web_fetch_timeout, follow_redirects=False) as client:
        results = await asyncio.gather(*(fetch_page(client, u, v) for u, v in targets), return_exceptions=True)
    for (u, v), r in zip(targets, results):
        if isinstance(r, Page):
            if r.note and v == "url":
                out.notes.append(f"{r.url}: {r.note}")
            if r.text:
                out.pages.append(r)
        elif v == "url":  # a page the user asked for explicitly: always say why it failed
            out.notes.append(f"Could not open {u}: {_why(r)}")
    return out


def _why(e: BaseException) -> str:
    if isinstance(e, httpx.TimeoutException):
        return "the site took too long to answer"
    if isinstance(e, httpx.HTTPError):
        return "connection problem"
    return str(e)[:120] or type(e).__name__


# ------------------------------------------------------------------ choosing what the model sees
def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


async def build_web_sources(pages: list[Page], query: str) -> list[dict]:
    """One source per page. If everything fits in the prompt budget it is passed whole; otherwise the passages
    most similar to the question are kept (ephemeral embeddings - nothing is stored)."""
    if not pages:
        return []
    s = get_settings()
    budget = s.web_context_chars
    excerpts: dict[int, str] = {}
    scores: dict[int, float | None] = {i: None for i in range(len(pages))}

    if sum(len(p.text) for p in pages) <= budget:
        excerpts = {i: p.text for i, p in enumerate(pages)}
    else:
        try:
            excerpts, scores = await _rank_passages(pages, query, budget)
        except Exception as e:  # noqa: BLE001 - e.g. embedding quota: fall back to the start of each page
            log.warning("passage ranking failed, using page starts: %s", e)
            share = max(1500, budget // len(pages))
            excerpts = {i: p.text[:share] for i, p in enumerate(pages)}

    return [
        {
            "type": "web", "filename": p.title[:120], "url": p.url, "location": _domain(p.url), "page": None,
            "kind": "web", "doc_id": None, "chunk_index": None, "score": scores[i],
            "text": excerpts[i], "snippet": excerpts[i][:400],
        }
        for i, p in enumerate(pages) if excerpts.get(i)
    ]


async def _rank_passages(pages: list[Page], query: str, budget: int) -> tuple[dict[int, str], dict[int, float]]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    from app.services.embeddings import embed_documents, embed_query

    splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=100)
    pieces: list[tuple[int, int, str]] = []  # (page index, position, text)
    for i, p in enumerate(pages):
        for j, t in enumerate(splitter.split_text(p.text)[:24]):
            pieces.append((i, j, t))
    vecs = await embed_documents([t for _, _, t in pieces])
    q = await embed_query(query)
    sims = [sum(a * b for a, b in zip(v, q)) for v in vecs]  # vectors are unit length -> dot = cosine

    chosen: set[int] = set()
    used = 0
    for i in range(len(pages)):  # guarantee: the best passage of every page
        idx = [k for k, (pi, _, _) in enumerate(pieces) if pi == i]
        if idx:
            k = max(idx, key=lambda k: sims[k]); chosen.add(k); used += len(pieces[k][2])
    for k in sorted(range(len(pieces)), key=lambda k: -sims[k]):
        if k not in chosen and used + len(pieces[k][2]) <= budget:
            chosen.add(k); used += len(pieces[k][2])
    excerpts, scores = {}, {}
    for i in range(len(pages)):
        mine = sorted(k for k in chosen if pieces[k][0] == i)
        if mine:
            excerpts[i] = "\n[...]\n".join(pieces[k][2] for k in mine)
            scores[i] = round((1 + max(sims[k] for k in mine)) / 2, 4)  # same 0-1 scale as Atlas scores
    return excerpts, scores