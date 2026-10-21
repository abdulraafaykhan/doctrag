# doctrag: bank assistant. Instructions for coding agents

Read this file first. Then read `docs/DESIGN.md` (what to build) and `docs/PHASES.md` (the order of work and the prompt for each phase).

## What this project is

An enterprise-style chatbot for a **fictional bank**. It is a portfolio demo and uses **fake data only**.

It answers from exactly three sources:
1. Approved bank documents (a shared knowledge base, loaded by a seed script).
2. The logged-in customer's **own** account data, fetched live through tools.
3. Files that same customer attached to the current chat.

It has **no web access** and **does not answer from the model's general knowledge**. Anything else gets a fixed refusal.

The code started as "doctrag", a general-purpose RAG backend. This work converts it. Existing pieces that stay: file loaders, chunking, Gemini embeddings, Atlas vector search, citation filter, SSE streaming.

## Stack

- Python, FastAPI, uv, Windows PowerShell. Project root `C:\Projects\doctrag`. Import root is `app` (for example `app.config`, `app.db`).
- MongoDB Atlas free tier (pymongo `AsyncMongoClient`, `$vectorSearch`). Vector index `chunks_vector_index`: 768 dims, cosine.
- Groq: answer model `llama-3.3-70b-versatile`, router model `llama-3.1-8b-instant` (settings `groq_model`, `groq_rewrite_model` in `app/config.py`; the values in `.env` win).
- Gemini `gemini-embedding-001`, 768 dimensions, normalised.
- Frontend (Phase 7 only): React + Vite.

## The folder on disk is the source of truth

Some files may differ from what these docs describe (for example `app/db.py`). **Read a file before you change it.** If reality contradicts these docs, say so in your report rather than guessing.

## Non-negotiable rules

### Security
1. **Never read, print, log or commit `.env`** or any secret. New settings go into `.env.example` with placeholder values. Check that `.gitignore` contains `.env`.
2. **Identity comes only from the verified login token** and is injected by the server. Never accept a user id, customer id or account id from a request body, query string, model output or tool argument. Tool schemas shown to the LLM must contain no identity fields.
3. Every read of user-owned data filters by the owner taken from the token. When a user asks for something they don't own, return **404**, not 403.
4. **The LLM is untrusted.** Its output, retrieved passages, uploaded files and tool results are *data*, never instructions.
5. Never log message text, balances, tokens or passwords. The audit log records events, not content.
6. Passwords are stored only as argon2 hashes. Money is stored as **integer minor units** plus a currency code, never as floats.
7. No web-access code anywhere: no search, no fetching of user-supplied URLs.

### Engineering
8. Work **one phase at a time** and stop at the end of each. Report: files changed, how to test it, anything that needs a human decision.
9. Do not rewrite working modules (`loaders.py`, `chunking.py`, `embeddings.py`, `citations.py`, `textfix.py`) unless the phase says so.
10. Keep the uv workflow. Use `uv add` and `uv remove`, then refresh `requirements.txt` with `uv export --no-hashes --no-dev -o requirements.txt`. `pyproject.toml` keeps `[tool.uv] package = false`.
11. Tests: pytest, run with `uv run pytest -q`. Tests stub Mongo and the LLM (existing tests stub `app.db` through `sys.modules`). Add tests with every phase. Never leave the suite red. Web-specific tests are deleted in Phase 1 along with the web code; every other existing test stays green.
12. Live services (Groq, Gemini, Atlas) cannot be tested from CI. For anything that touches them, list the exact manual check the human should run.
13. Use Pydantic models for every request and response. Swagger must stay usable: the **Authorize** button works, and examples are pre-filled.
14. Write small, typed functions. Comments explain *why*, not *what*.
15. Give commands as **PowerShell**. No bash-only syntax in instructions.
16. **Free-tier budget:** do not add LLM calls beyond those in `docs/DESIGN.md`. Do not increase prompt size without checking the token budget section.
17. If a decision is not covered by these docs, **ask**. Never guess on anything security-related.

## Gotchas already known

- `app/main.py` contains `_fix_binary`, an OpenAPI patch that makes Swagger show a real file picker. Keep it.
- `$vectorSearch` can only filter on fields that are declared in the Atlas index definition. Changing the index definition needs the index to be rebuilt (wait until it is READY).
- Documents can get stuck in `processing` if the server restarts during ingestion. Add a startup sweep that marks old `processing` documents as `failed`.
- Embeddings are batched (50 per call) with retry on 429/500/503. Keep that behaviour.
- Windows and Python 3.12-style typing are assumed (`list[str]`, `X | None`).

## Definition of done for a phase

- The phase's acceptance checks in `docs/PHASES.md` pass.
- `uv run pytest -q` is green.
- `requirements.txt` and `.env.example` are updated if dependencies or settings changed.
- A short report is written, ending with the exact PowerShell commands the human should run to verify.

## What you may run yourself

You may run: `uv add`, `uv remove`, `uv sync`, `uv export ...`, `uv run pytest -q`, `uv run python -m scripts.<name>` for scripts that don't need live keys, and `npm install` or `npm run build` inside `frontend/` (Phase 7).

Do not run: anything that needs my real API keys or database (the seed scripts, the evaluation script, `uvicorn` against Atlas). Give me the exact PowerShell commands instead and I will run them.

Do not run `git commit` or `git push` unless I ask. I commit after checking each phase.

Never open, print or edit `.env`. When a new setting is needed, add a placeholder line to `.env.example` and list the setting names in your report.