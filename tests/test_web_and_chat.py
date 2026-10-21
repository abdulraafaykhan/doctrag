import asyncio, os, sys, threading, http.server, json
os.environ.update(MONGODB_URI="x", GROQ_API_KEY="x", GEMINI_API_KEY="x")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import types
import pytest
# Tests never touch MongoDB: stub the db module before anything imports it.
_db = types.ModuleType("app.db")
_db.documents_col = _db.chunks_col = _db.messages_col = lambda: None
sys.modules["app.db"] = _db
from app.services import web

def run(c): return asyncio.run(c)

# ---------- SSRF guard ----------
@pytest.mark.parametrize("u", ["http://127.0.0.1/", "http://localhost:80/", "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.5/", "http://192.168.1.1/", "http://[::1]/", "http://[::ffff:127.0.0.1]/", "file:///etc/passwd",
    "ftp://x.com/", "http://user:pw@example.com/", "http://example.com:22/", "http://0.0.0.0/"])
def test_blocks_internal(u):
    assert run(web.check_public_url(u)) is not None

def test_allows_public_ip():
    assert run(web.check_public_url("https://8.8.8.8/")) is None

# ---------- fetching/extraction against a local server (guard bypassed only in the test) ----------
HTML = ("<html><head><title>Acme Widgets</title></head><body><nav>Home | Shop</nav><article><h1>Acme Widgets</h1>"
        + "<p>Acme makes widgets in Berlin since 1999. Their flagship Widget Pro costs 49 euros.</p>" * 6
        + "</article><script>var x=1</script></body></html>")
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/redir":
            self.send_response(302); self.send_header("Location", "/page"); self.end_headers(); return
        if self.path == "/evil":
            self.send_response(302); self.send_header("Location", "http://169.254.169.254/x"); self.end_headers(); return
        if self.path == "/404":
            self.send_response(404); self.end_headers(); return
        if self.path == "/empty":
            body = b"<html><body><div id=app></div><script>1</script></body></html>"
        else:
            body = HTML.encode()
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body)

@pytest.fixture(scope="module")
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()

@pytest.fixture
def open_guard(monkeypatch):
    real = web.check_public_url
    async def lax(url):
        return None if url.startswith("http://127.0.0.1") else await real(url)
    monkeypatch.setattr(web, "check_public_url", lax)
    monkeypatch.setattr(web, "ALLOWED_PORTS", {None, 80, 443, 8080, 8443} | set(range(1024, 65536)))

def test_fetch_extracts_main_text(server, open_guard):
    r = run(web.gather_web([server + "/redir"], None))
    assert not r.notes and len(r.pages) == 1
    p = r.pages[0]
    assert p.title == "Acme Widgets" and "Widget Pro costs 49 euros" in p.text and "var x" not in p.text

def test_redirect_to_internal_is_blocked(server, open_guard):
    r = run(web.gather_web([server + "/evil"], None))
    assert not r.pages and "not a public website" in r.notes[0]

def test_http_error_and_empty_page_reported(server, open_guard):
    r = run(web.gather_web([server + "/404", server + "/empty"], None))
    joined = " ".join(r.notes)
    assert "HTTP 404" in joined

def test_search_by_name_reads_top_results(server, open_guard, monkeypatch):
    async def fake_search(q, n=None): return [{"title": "t", "url": server + "/page", "snippet": ""}, {"title": "t2", "url": server + "/404", "snippet": ""}]
    monkeypatch.setattr(web, "web_search", fake_search)
    r = run(web.gather_web([], "Acme Widgets official website"))
    assert len(r.pages) == 1 and r.pages[0].via == "search" and not r.notes  # a dead search result is skipped silently

def test_url_plus_query_adds_site_pages(server, open_guard, monkeypatch):
    seen = {}
    async def fake_search(q, n=None): seen["q"] = q; return [{"title": "x", "url": server + "/redir", "snippet": ""}]
    monkeypatch.setattr(web, "web_search", fake_search)
    r = run(web.gather_web([server + "/page"], "pricing"))
    assert seen["q"] == "site:127.0.0.1 pricing" and len(r.pages) == 2

def test_search_failure_gives_note(monkeypatch):
    async def none(q, n=None): return []
    monkeypatch.setattr(web, "web_search", none)
    r = run(web.gather_web([], "anything"))
    assert not r.pages and "no results" in r.notes[0]

def test_build_sources_whole_page_when_small():
    pg = [web.Page("https://a.com/x", "A", "hello " * 50), web.Page("https://www.b.com", "B", "world " * 50)]
    src = run(web.build_web_sources(pg, "q"))
    assert [s["location"] for s in src] == ["a.com", "b.com"] and all(s["type"] == "web" for s in src)

def test_build_sources_ranks_when_large(monkeypatch):
    big = [web.Page("https://a.com", "A", ("filler text. " * 400) + " The secret code is 4242. " + ("more filler. " * 400))]
    import types
    emb = types.ModuleType("app.services.embeddings")
    async def ed(texts): return [[1.0, 0.0] if "4242" in t else [0.0, 1.0] for t in texts]
    async def eq(t): return [1.0, 0.0]
    emb.embed_documents, emb.embed_query = ed, eq
    monkeypatch.setitem(sys.modules, "app.services.embeddings", emb)
    src = run(web.build_web_sources(big, "what is the code"))
    assert "4242" in src[0]["text"] and len(src[0]["text"]) <= 7500 and src[0]["score"] == 1.0

def test_build_sources_falls_back_if_embedding_fails(monkeypatch):
    import types
    emb = types.ModuleType("app.services.embeddings")
    async def boom(*a): raise RuntimeError("quota")
    emb.embed_documents = emb.embed_query = boom
    monkeypatch.setitem(sys.modules, "app.services.embeddings", emb)
    src = run(web.build_web_sources([web.Page("https://a.com", "A", "x" * 20000)], "q"))
    assert 0 < len(src[0]["text"]) <= 7000

# ---------- full chat flow with fakes ----------
def _chat(monkeypatch, plan, doc_hits, tokens, web_sources=None, pages=None, mode="auto", names_list=None, calls=None):
    from app.routers import chat
    from app.services.router import Plan
    saved = []
    async def names(*a, **k): return ["report.pdf"] if names_list is None else names_list
    async def hist(*a, **k): return [{"role": "assistant", "content": "earlier answer [1]"}]
    async def pl(*a, **k): return plan
    async def srch(q, doc_ids=None): return doc_hits
    async def save(sid, role, content, **kw): saved.append((role, content, kw))
    async def gw(urls, q):
        return web.WebResult(pages=pages or [], notes=["Could not open x"] if pages is None and urls else [])
    async def stream(history, q, sources, notes, searched, mode="auto"):
        if calls is not None: calls.append(mode)
        for t in tokens: yield t
    for k, v in dict(ready_document_names=names, get_recent=hist, plan_turn=pl, search=srch, save_message=save, gather_web=gw, stream_answer=stream).items():
        monkeypatch.setattr(chat, k, v)
    from app.schemas import ChatRequest
    async def go():
        return [e async for e in chat.run_chat(ChatRequest(message="hi", session_id="s1", web=mode))]
    return run(go()), saved

HIT = lambda sc: {"filename": "report.pdf", "text": "Revenue was 10M.", "page": 2, "doc_id": "d1", "chunk_index": 0, "score": sc, "location": "p.2", "kind": "text"}

def test_flow_docs_with_clean_citations(monkeypatch):
    from app.services.router import Plan
    evs, saved = _chat(monkeypatch, Plan("q", use_documents=True), [HIT(0.9), HIT(0.3)],
                       ["Revenue was 10M", "【1†L1-", "L4】 and 【7】."])
    kinds = [e["type"] for e in evs]
    assert kinds[0] == "status" and "meta" in kinds and kinds[-1] == "done"
    meta = next(e for e in evs if e["type"] == "meta")
    assert len(meta["sources"]) == 1 and meta["sources"][0]["n"] == 1 and "text" not in meta["sources"][0]  # weak hit dropped
    text = "".join(e["t"] for e in evs if e["type"] == "token")
    assert text == "Revenue was 10M[1] and." or text.replace(" .", ".") == "Revenue was 10M[1] and."
    assert evs[-1]["cited"] == [1]
    assert saved[1][1] == text and saved[1][2]["sources"][0]["cited"] is True

def test_flow_no_sources_is_plain_chat(monkeypatch):
    from app.services.router import Plan
    evs, saved = _chat(monkeypatch, Plan("q"), [], ["Hello ", "there!"])
    meta = next(e for e in evs if e["type"] == "meta")
    assert meta["sources"] == [] and evs[-1]["cited"] == []
    assert "".join(e["t"] for e in evs if e["type"] == "token") == "Hello there!"

def test_flow_web_and_docs_numbered_together(monkeypatch):
    from app.services.router import Plan
    pages = [web.Page("https://acme.com/p", "Acme", "Acme sells widgets. " * 30)]
    evs, _ = _chat(monkeypatch, Plan("q", use_documents=True, search_web=True, urls=["https://acme.com/p"]), [HIT(0.8)],
                   ["Doc says A [1]; site says B [2]."], pages=pages)
    meta = next(e for e in evs if e["type"] == "meta")
    assert [(s["n"], s["type"]) for s in meta["sources"]] == [(1, "document"), (2, "web")]
    assert meta["sources"][1]["url"] == "https://acme.com/p" and evs[-1]["cited"] == [1, 2]

def test_flow_web_failure_reaches_user(monkeypatch):
    from app.services.router import Plan
    evs, _ = _chat(monkeypatch, Plan("q", search_web=True, urls=["https://x.com"]), [], ["ok"])
    assert next(e for e in evs if e["type"] == "meta")["notes"] == ["Could not open x"]


# ---------- web switch: off = pure document RAG, on = web always, auto = smart ----------
def test_off_blocks_web_questions_without_calling_the_model(monkeypatch):
    from app.services.router import Plan
    calls = []
    evs, saved = _chat(monkeypatch, Plan("q", wants_web=True, wants_docs=False), [], ["SHOULD NOT APPEAR"], mode="off", calls=calls)
    text = "".join(e["t"] for e in evs if e["type"] == "token")
    assert "turned off" in text and "SHOULD NOT" not in text and calls == []
    assert evs[-1]["type"] == "done" and saved[1][1] == text   # the exchange is still saved in the history

def test_off_without_documents_says_so(monkeypatch):
    from app.services.router import Plan
    calls = []
    evs, _ = _chat(monkeypatch, Plan("q"), [], ["x"], mode="off", names_list=[], calls=calls)
    assert "none are ready" in "".join(e["t"] for e in evs if e["type"] == "token") and calls == []

def test_off_document_question_is_answered_in_strict_mode(monkeypatch):
    from app.services.router import Plan
    calls = []
    evs, _ = _chat(monkeypatch, Plan("q", use_documents=True, wants_docs=True), [HIT(0.9)], ["Revenue was 10M [1]."], mode="off", calls=calls)
    assert calls == ["off"] and evs[-1]["cited"] == [1]
    assert next(e for e in evs if e["type"] == "meta")["plan"]["web"] is False

def test_on_and_auto_pass_their_mode_to_the_model(monkeypatch):
    from app.services.router import Plan
    for mode in ("on", "auto"):
        calls = []
        _chat(monkeypatch, Plan("q"), [], ["hi"], mode=mode, calls=calls)
        assert calls == [mode]

def test_apply_mode_rules():
    from app.services.router import Plan, apply_mode
    def p(): return Plan("q", wants_docs=False, wants_web=True, urls=["https://a.com"], web_query="x")
    off = apply_mode(p(), "off", ["f.pdf"])
    assert off.use_documents and not off.search_web and off.urls == []          # docs always, web never
    assert not apply_mode(p(), "off", []).use_documents                         # nothing to search
    on = apply_mode(Plan("q", wants_docs=False), "on", ["f.pdf"])
    assert on.search_web and on.web_query == "q" and not on.use_documents       # web always; docs only if relevant
    auto = apply_mode(Plan("q", wants_docs=True, wants_web=False, urls=["u"]), "auto", ["f.pdf"])
    assert auto.use_documents and not auto.search_web and auto.urls == []       # planner decides

def test_planner_ignores_invented_urls_and_respects_mode(monkeypatch):
    import types
    from app.services import router
    data = {"standalone_question": "What does Acme charge?", "use_documents": False, "search_web": True,
            "web_query": "Acme pricing", "urls": ["https://invented.example/x", "https://acme.com/pricing"]}
    async def create(**kw):
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps(data)))])
    fake = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    monkeypatch.setattr(router, "client", lambda: fake)
    plan = run(router.plan_turn([], "What does acme.com/pricing say about Acme?", ["f.pdf"], "auto"))
    assert plan.search_web and plan.urls == ["https://acme.com/pricing"] and plan.question == "What does Acme charge?"
    off = run(router.plan_turn([], "What does acme.com/pricing say about Acme?", ["f.pdf"], "off"))
    assert off.wants_web and not off.search_web and off.use_documents   # wants the web, switch is off -> chat tells the user

# ---------- formatting clean-up ----------
def test_textfix_normalises_lookalike_characters():
    from app.services.textfix import normalize
    assert normalize("|\u2011\u2011\u2011|") == "|---|"
    assert normalize("175\u202fB and 16\u2009k") == "175 B and 16 k"
    assert normalize("a\u200bb") == "ab"