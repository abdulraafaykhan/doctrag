# BUILD_PROMPTS.md — paste one phase at a time into your coding agent

Four phases, three test gates. Open the agent in `C:\Projects\bank_agent` (branch `bank-agent`). The files from this pack, `requirements*.txt` and an activated `.venv` must already be in place. Copy `.env.example` to `.env` and fill the keys yourself.

Before Phase 1, you do these by hand (the agent cannot):
1. Create a free MongoDB Atlas M0 cluster (or reuse the doctrag one with a NEW database name `harbourline`), allow your IP, copy the connection string into `.env`.
2. Get a Groq key (console.groq.com) and a Gemini key (AI Studio) into `.env`.
3. Confirm the model ids in `config/settings.yaml` against both dashboards.

---

## PHASE 1 — Foundation

```
Read CLAUDE.md, docs/01-architecture.md, docs/02-data-model.md and docs/06-frontend-and-logging.md (logging section only).
We are building a NEW project in this folder. Do not touch C:\Projects\doctrag.

Build the foundation:
1. Project skeleton: app/ package, pyproject.toml kept in sync with requirements.in (uv init --no-workspace if needed, then uv add -r requirements.in; keep requirements.txt as the lock), tests/, scripts/, data/ (git-ignored), seed/.
2. app/config.py: load config/*.yaml and prompts/ and .env with pydantic-settings; one cached settings object; fail fast with a clear message for a missing key.
3. app/log.py exactly as docs/06 describes (rich console, areas, log.step, @traced, log.span, PII masking helpers, rotating JSON file log). Write masking helpers (mask_mobile, mask_account, mask_text) here; they are reused everywhere.
4. app/db.py: ONE MongoClient, collection accessors, scripts/create_indexes.py (normal indexes, TTLs, the 2 Atlas search indexes; poll until READY).
5. app/llm.py: one LLMClient with chat() and vision() and embed(): Groq (groq SDK), Gemini (google-genai), the fallback chain from config, tenacity retry on 429/timeouts, quota guard backed by quota_usage, a per-call log line. Provide a FakeLLM for tests.
6. seed/*.json (10 fake customers with users, accounts, cards, 40+ transactions per account, one frozen account, one blocked card, obviously fake mobile and account numbers) and scripts/seed_db.py (idempotent).
7. app/money.py: paise <-> rupees formatting with Indian digit grouping, plus the emi calculation function.
8. FastAPI app (app/main.py) with only /api/health for now, startup checks (DB ping, index status, keys present, models configured) printed to the terminal.

Test only: money formatting, masking, config loading, quota guard. Run those, run scripts/seed_db.py against Atlas, start the server and call /api/health.
Commit "add foundation: config, logging, db, llm client, seed data".
Reply with the summary rules from CLAUDE.md (under 15 lines).
```

---

## PHASE 2 — Parsing, images, RAG, uploads → TEST GATE 1

```
Read CLAUDE.md and docs/05-rag-and-parsing.md, docs/02-data-model.md (knowledge section), docs/03-tools.md (search and lookup tools).

Build the knowledge pipeline:
1. app/ingest/: one pipeline used by both the admin script and customer uploads: validate (filetype, size, limits), hash/dedupe, parse per file type, image cascade, chunk, embed, store, with resumable ingest_jobs and the quota guard. Every parser emits the shared element format.
2. Parsers: PDF (pymupdf + pdfplumber, per-page text-layer check), PPTX (text, native tables, notes, embedded pictures), DOCX, XLSX/CSV (header detection, rate_rows), MD/TXT. No Docling.
3. Image cascade exactly as docs/05: image_cache, RapidOCR triage, classification, VLM only for hard crops using prompts/vlm_table.txt and vlm_screenshot.txt, dual extraction for numbers, validation, review flags.
4. Chunking and embeddings exactly as docs/05 (parent sections, table row chunks, context prefix, 768 dims normalized, embed_cache, pacing).
5. Retrieval: hybrid vector + keyword with Python RRF, filters, optional reranker behind the feature flag (off), no_good_match threshold. Functions: search_knowledge(...) and lookup_rate(...).
6. scripts/make_sample_docs.py: generate the fictional Harbourline sample documents listed in docs/05 into data/bank_docs/ (include rate tables as images inside a PDF and a PPTX, and a superseded older rate card). scripts/ingest_bank_docs.py ingests them with a progress display and resumes after quota pauses.
7. Upload service functions (used later by the API): save file, enforce limits, create attachment record, background processing with status, delete, TTL cleanup job.

TEST GATE 1: write and run the tests in docs/07-tests.md "Gate 1". Use a small set of real calls for embeddings and the VLM; cache results in data/test_cache/. Fix failures. Report hit rate, extraction accuracy and the number of flagged items. Do not move on while any gate item fails.
Commit "add parsing, image cascade, rag retrieval, uploads". Reply in under 15 lines.
```

---

## PHASE 3 — Auth, flows, tools, agent → TEST GATE 2

```
Read CLAUDE.md, docs/03-tools.md, docs/04-auth-and-flows.md, docs/01-architecture.md, prompts/agent_system.txt and prompts/agent_blocks.yaml, config/flows/*.yaml, config/messages.yaml.

Build the conversation engine:
1. app/session.py: sessions store, auth state machine, held messages, replay, locks, idle expiry, OTP hash with verify_otp() boundary (demo mode from config).
2. app/prepass.py: regex extraction (mobile with optional +91 and spaces, 12-digit account with spaces or dashes, 6-digit code, commands), PII masking, OTP/PIN never reach history, audit log or prompts.
3. app/flows.py: the YAML-driven flow engine (slots, validators, synonyms, buttons, summary, confirm/change/cancel in code, pending_actions, atomic submit with idempotency key, reference numbers). Cheque book pages are 20, 40, 60 only.
4. app/tools/: every tool in docs/03 as CrewAI tools with pydantic args and the envelope. Factories bind the session so the model never supplies user_id or account_id. app/tools/registry.py builds the tool list from auth state and flow state exactly as the visibility table says.
5. app/agent.py: build one CrewAI agent per turn (system prompt from prompts/ with injected blocks), run with the step/tool limits, fallback to the next model on 429/timeout, collect tool displays, run the number check and the one repair attempt, return reply, citations, options, state.
6. app/verify.py: number extraction and comparison (8.5 equals 8.50%), citation check.
7. app/chat.py: the single turn handler implementing docs/01 steps 1-10.

TEST GATE 2: write tests for groups A, B, C, G, I, J from docs/07-tests.md using FakeLLM for code paths, and run groups B, G, H against the real Groq model (security and flows 3 times each). Then run the model bake-off from docs/07 and record the chosen smallest model in config/settings.yaml. Fix until the release bar for auth, flows and security is met.
Commit "add auth, flows, tools, agent". Reply in under 15 lines, include which model you chose and its tool-call success rate.
```

---

## PHASE 4 — API, React UI, logging, integration → TEST GATE 3, then one refactor

```
Read CLAUDE.md and docs/06-frontend-and-logging.md and docs/07-tests.md (Gate 3).

1. API routes from docs/06 in FastAPI (session, chat, upload, upload status, delete, files, ui-event, health), second-tab 409, serving frontend/dist at /. Every route logs via app/log.py.
2. React app in frontend/ (Vite, react 19, react-markdown + remark-gfm, plain CSS) following docs/06 exactly: minimal clean layout, sent messages visible at once, attachment cards with an x remove button BEFORE sending, cards that stay visible and openable AFTER sending, option buttons, citation chips, login chip, status lines, no overlapping or clipped elements at 360px and 1280px. Send every button click and UI action to /api/ui-event.
3. Make sure the terminal shows a log line for every click, upload, query, tool call, library/model call and response (see the table and example in docs/06). Check by running a full conversation and reading the terminal output; fix silent gaps.
4. scripts/run_dev.ps1 to start backend and frontend, and a README.md with setup (uv, Node 20+, .env, seed, create_indexes, ingest sample docs, run).

TEST GATE 3: API tests with TestClient, npm run build, then run the end-to-end set from docs/07 (about 30 mixed questions, 5 flows, 5 attacks) and write eval/last_report.md. Do the manual UI checklist yourself by starting the app and describing what you verified (use a headless browser such as playwright only if it is already installed; do not install heavy tooling).

FINAL REFACTOR (one pass, behaviour unchanged): remove dead code, shorten long functions, make comments explain why, align names, check that no model name, limit or wording is hardcoded, run the full test suite once.
Commit "add api and react ui; final cleanup". Reply in under 15 lines with the release-bar results.
```

---

## If something fails
- Atlas search index not READY: wait a few minutes, rerun `scripts/create_indexes.py`.
- Gemini 429: ingestion pauses by design; rerun later, it resumes.
- A model id is rejected: fix it in `config/settings.yaml` only.
- Install problem on Windows: send the last 15 lines of the error.
