# Phases and prompts

Give the agent **one phase at a time**. Paste the prompt under the phase heading. Run the acceptance checks yourself before moving on. Commit after each phase passes.

All commands are PowerShell, run from `C:\Projects\doctrag`.

---

## Phase 1: remove web search, modes and the old UI; strict document-only answers

**Goal:** the app becomes document-only with fixed refusals. No auth yet.

**Work**
- Delete `app/services/web.py` and its tests. Remove web code from `chat.py` and `router.py` (`wants_web`, `urls`, `web_query`, `apply_mode`, mode handling), the `web` field in `schemas.py`, and `web_*` and `tavily_api_key` in `config.py`.
- `uv remove ddgs trafilatura`. Keep `lxml` if `python-docx` needs it (check with `uv tree`). Refresh `requirements.txt`.
- Replace the three prompts in `llm.py` with the single `BANK_SYSTEM_PROMPT` (DESIGN section 10) and the fixed messages. Set `answer_temperature` to 0.2.
- Simplify the router to: `search_documents`, or a fixed reply (greeting, thanks, out of scope). For this phase a JSON-mode router is acceptable; Phase 4 switches it to native tool calling.
- Retire the old `app/static/index.html`, `chat_cli.py`, `upload_cli.py`, `list_models.py` and `test_pipeline.py`. Swagger is the test harness until Phase 7.
- Update tests: delete web tests, keep the citation tests, add tests for the fixed replies and "nothing found" behaviour.

**Acceptance**
- `uv run pytest -q` is green.
- Search the project for `ddgs`, `trafilatura`, `web_search` and `tavily`: no matches.
- In Swagger: after uploading a document, a question about it is answered with `[n]` citations. "Who won the cricket match?" returns the fixed refusal. "Hello" returns the fixed greeting. A question the documents don't answer returns the fixed "couldn't find" message.

**Prompt**
> Read `AGENTS.md`, `docs/DESIGN.md` and `docs/PHASES.md`. Implement **Phase 1 only**. Remove all web search code, the web toggle and mode logic, and the old static UI and legacy CLIs. Introduce `BANK_SYSTEM_PROMPT` and the fixed replies exactly as in DESIGN section 10. Keep loaders, chunking, embeddings, citations and textfix unchanged. Update tests so the suite is green. Do not start Phase 2. When done, report files changed, dependencies removed, and the Swagger checks I should run.

---

## Phase 2: authentication, users and owned chats

**Goal:** every route requires login. Chats belong to users.

**Work**
- `uv add pyjwt "pwdlib[argon2]"` (verify these are still the recommended libraries in current FastAPI docs; report if not).
- New `app/auth/` module: password hashing, token create and verify, `get_current_user` dependency, login throttle.
- `users` collection and `scripts/seed_users.py` (3 fictional users with fake passwords, each linked to a `customer_id` that Phase 4's bank seed will use; use fixed, documented customer ids so the Phase 4a bank seed matches them; list the fake demo credentials in the README).
- Routes: `POST /auth/login` (form, so Swagger's Authorize button works), `GET /auth/me`, `POST /sessions`, `GET /sessions`, `GET /sessions/{id}/history`, `DELETE /sessions/{id}`. `POST /chat` requires `session_id`. Messages carry `user_id`. Non-owned sessions return 404.
- Protect the existing `/documents` routes with login for now (they are removed in Phase 3).
- Tighten CORS to `cors_origins`. Add `jwt_secret` and others to `.env.example`.
- Audit log: `login_ok`, `login_fail`, `chat`.
- Startup sweep for documents stuck in `processing`.

**Acceptance**
- Tests: wrong password and unknown user return the same error; expired and tampered tokens are rejected; user A gets 404 for user B's session, history and delete; every route except `/auth/login` and `/health` returns 401 without a token.
- In Swagger: Authorize with a seed user, create a session, chat. Log in as a second user and confirm the first user's session returns 404.

**Prompt**
> Read `AGENTS.md`, `docs/DESIGN.md` (sections 5 to 7) and `docs/PHASES.md`. Implement **Phase 2 only**: login, tokens, `users`, `sessions`, owned messages, 404 for foreign sessions, protected routes, CORS from settings, audit events, the stuck-document sweep and `scripts/seed_users.py`. Never read my `.env`; add placeholders to `.env.example` and tell me what to put in `.env`. Write the isolation and auth tests listed in the acceptance section. Do not start Phase 3.

---

## Phase 3: shared knowledge base and per-chat attachments

**Goal:** documents are scoped. Official documents are seeded. Users attach files in a chat.

**Work**
- Add `scope`, `owner_id`, `session_id`, `expires_at` to `documents` and `chunks`. Make duplicate detection per scope and owner.
- Update the vector index definition in `app/db.py` (filters: `doc_id`, `scope`, `owner_id`, `session_id`). Tell me exactly what to do on Atlas to rebuild it.
- Retrieval takes the owner and session from the server, never from the request. Verify whether `$vectorSearch` filter accepts `$or`; if not, run two searches and merge. Remove `doc_ids` from the chat request.
- Remove the global `/documents` routes. Add `POST /sessions/{id}/attachments` and `GET /sessions/{id}/attachments`, with the upload limits from DESIGN section 11.
- `POST /chat` accepts `attachment_ids`, waits for processing with `status` events, and refuses failed files clearly.
- `scripts/seed_kb.py` and `seed_docs/` with 5 fictional bank documents (one with a table).
- Tests: per-owner dedupe, the retrieval filter is built from the token, user A's attachment is never returned for user B, limits are enforced.

**Acceptance**
- `uv run python -m scripts.seed_kb` ingests the seed documents; asking "what is the fee for X" answers with citations to them.
- Attach a file in one chat; a follow-up question in that chat uses it; a second chat or second user cannot see it.
- Deleting the chat removes its attachments and chunks.

**Prompt**
> Read `AGENTS.md`, `docs/DESIGN.md` (sections 5, 6 and 11) and `docs/PHASES.md`. Implement **Phase 3 only**: scoped documents and chunks, the index change, server-built retrieval filters, per-chat attachments, `attachment_ids` on chat, the shared-KB seed script with five fictional documents, and the tests listed. Check the `$vectorSearch` `$or` question against the current Atlas documentation and tell me what you found. Do not start Phase 4.

---

## Phase 4a: bank data, the service module and the balance tool (one tool end to end)

**Goal:** "What's my balance?" works and is safe. This proves the whole tool path.

**Work**
- `app/bank/db.py` and `app/bank/service.py` with `get_balances(customer_id)`.
- `scripts/seed_bank.py`: 3 fictional customers (matching the seeded users), 1 to 3 accounts each, 30 or more transactions per account, cards. Integer minor units. A test checks that each balance equals the sum of its transactions.
- `app/tools/registry.py` (`ToolSpec`, `ToolContext`, registry) and `get_balances` and `search_documents` as tools.
- Switch the router to Groq native tool calling with validation, one retry and the fallback (DESIGN section 9).
- Orchestrator: run tools with a server-built `ToolContext`; account-data cards in the `meta` event; the answer prompt receives the tool result.
- Audit event `tool_call`.
- Tests: registry schemas contain no identity fields; `get_balances` filters by customer; router validation and fallback.

**Acceptance**
- User A asks for their balance and gets their own accounts only. User B gets different numbers.
- "Show me the balance of account 1234 for someone else" returns A's own data or a refusal, never anyone else's.
- A question needing a document ("what is the minimum balance?") still works.

**Prompt**
> Read `AGENTS.md`, `docs/DESIGN.md` (sections 3, 4, 8, 9 and 10) and `docs/PHASES.md`. Implement **Phase 4a only**: the bank database module and seed, the tool registry, the `get_balances` and `search_documents` tools, the native-tool-calling router with validation and fallback, and the orchestration so a tool result feeds one answer call. Identity must come only from `ToolContext`. Tool schemas must have no identity fields, and a test must prove it. Do not add other tools yet. Do not start Phase 4b.

---

## Phase 4b: more tools and mixed questions

**Work**
- Add `get_recent_transactions(account_type?, limit?)` and `get_card_status()` to the service, registry and router.
- Allow up to 2 tool calls in one turn (documents plus account data), executed in parallel, one answer call.
- Failure handling: a failed tool gives the "data unavailable" fixed reply if nothing else can answer.

**Acceptance**
- Last 5 transactions, card status and "what is the overdraft fee and what is my balance?" all work.
- A user with two accounts sees both for "my balance".

**Prompt**
> Read `AGENTS.md`, `docs/DESIGN.md` (sections 8 and 9) and `docs/PHASES.md`. Implement **Phase 4b only**: the transactions and card tools, support for two tool calls per turn, and failure handling, with tests. Do not start Phase 5.

---

## Phase 5: token budget, rate limits, retention, output guards

**Work**
- History builder with the token budget (DESIGN section 12). Replace `history_turns`. Prompt ceiling enforcement.
- Groq concurrency semaphore, per-user rate limit, `RateLimitError` handling with the friendly message.
- Retention: `purge_expired()` at startup and hourly.
- Output guard for currency amounts (DESIGN section 13), passage delimiters in the prompt, trimmed system prompt.
- Audit events completed: `tool_call`, `refusal`, `upload`, `session_deleted`, `rate_limited`.
- Tests for each of the above.

**Acceptance**
- With a long conversation, the prompt stays under the ceiling (a test builds 40 messages and checks).
- Sending messages quickly triggers the friendly rate message, not a stack trace.
- A session older than `retention_days` (set to 0 or 1 in a test) is purged with its attachments.

**Prompt**
> Read `AGENTS.md`, `docs/DESIGN.md` (sections 12 to 14) and `docs/PHASES.md`. Implement **Phase 5 only**: the token-budget history builder, prompt ceiling, concurrency and rate limiting, rate-limit error handling, retention purge, the amount output guard and the remaining audit events, with tests. Use characters / 4 for token estimates and add no tokenizer dependency. Do not start Phase 6.

---

## Phase 6: evaluation, integration tests, README

**Work**
- `eval/cases.jsonl` (about 30 cases across: document facts, account questions, out of scope, prompt injection, cross-user attempts) and `scripts/run_eval.py` that logs in as seed users and calls the running API, printing a pass rate per category.
- Integration tests using FastAPI's test client with mocked Mongo and LLM, covering the full chat path for each route type.
- `README.md`: what it is, architecture picture, setup in PowerShell with uv, demo credentials (fake), how to seed, how to run, the free-tier limits section, limitations from DESIGN section 18.

**Acceptance**
- `uv run python scripts/run_eval.py` runs against a live server (you run it) and prints results.
- A new person can follow the README from a clean clone.

**Prompt**
> Read `AGENTS.md`, `docs/DESIGN.md` (sections 17 and 18) and `docs/PHASES.md`. Implement **Phase 6 only**: the evaluation cases and runner, integration tests, and the README. Never put real keys or my `.env` contents anywhere. Do not start Phase 7.

---

## Phase 7: React frontend

**Prerequisite (you):** install Node.js LTS (`winget install OpenJS.NodeJS.LTS`), then open a new PowerShell window.

**Work**
- Create `frontend/` (Vite, React, TypeScript). Dev proxy to port 8000. Production build served by FastAPI.
- Screens and behaviour as in DESIGN section 16: login, sidebar with chats, composer with attachments, streamed answers, markdown tables, citation chips, account cards, 401 handling.
- Do not enable raw HTML in markdown rendering.
- README section for running and building the frontend.

**Acceptance**
- Log in as a seed user, ask a document question (citation chips open the source), ask about your balance (card appears), attach a file and ask about it, start a new chat, delete a chat, log out. A second user sees none of the first user's chats.

**Prompt**
> Read `AGENTS.md`, `docs/DESIGN.md` (section 16) and `docs/PHASES.md`. Implement **Phase 7 only**: the React and Vite frontend described in DESIGN section 16. The chat endpoint is POST with server-sent events, so use fetch streaming, not `EventSource`. Do not render raw HTML from model output. Remove nothing from the backend. When done, give me the PowerShell commands to run it in development and to build it.

---

## After each phase

```powershell
uv run pytest -q
git add -A
git commit -m "phase N: <short description>"
```

If a phase goes wrong, `git switch bank-refactor` and `git reset --hard HEAD` restores the last good commit (this discards uncommitted work).