# Phases

## Done (history, do not redo)

Phases 1 to 7: document-only bot, tools, token budget, rate limits, retention, evaluation, React frontend. R1: anonymous sessions, in-chat verification, ten fake customers, one-tab lock. Plus the personal-intent safety net. Details are in `docs/REDESIGN.md` section 1.

Dropped: OCR, CrewAI.

## R2: everything, in one run, in six stages

One prompt, six stages in this order. After **each stage** the backend and frontend tests are green. If your context is running low, stop at the end of a stage and report. Read the docs each stage names; do not read the rest.

### Stage 1: logging, visible chat, the post-OTP bug
Read: `CODE_RULES.md`, `REDESIGN.md` sections 3, 6, 7, `LOGGING.md`.
- Logging foundation exactly as `LOGGING.md`: the layout, all events, `turn_done`, provider errors with status and message, `LOG_LEVEL`, `LOG_FULL_PROMPTS`. Remove `LOG_SHOW_SENSITIVE`. Set the model defaults to the gpt-oss ids.
- Store the user's message **first** in every turn. Render every message in the frontend with a bubble, optimistic send, error bubbles that keep the user's bubble, masked display policy A. Make `GET /sessions/{id}/history` return `role`, `text`, `options`, `cards`, `sources`, `created_at`.
- Fix the post-OTP replay: store the intended action when verification starts and run it directly after the OTP (no router).
- Tests: a failing turn keeps the user message; history round trip; the replay never calls the router; provider 404 logs status and message but no chat content.
Acceptance: "what is my balance" in a fresh chat, after account, mobile and OTP, shows the account card; every message from both sides is visible and stays after a refresh.

### Stage 2: routing and the flow engine
Read: `REDESIGN.md` sections 3, 4, 8, `SERVICE_FLOWS.md` sections 1 and 2.
- The turn pipeline in the order of `REDESIGN.md` section 3: flow engine first, then the rules layer, then one router call, fallback, executor with the verification check, answer.
- The generic flow engine (data-driven flows, option validation, cancel, unrelated-question handling, verification guard, idempotent finish). Move verification onto this engine. Add option buttons end to end (stored, streamed, rendered, click sends `option_id`).
- Tool set as in `REDESIGN.md` section 4: `search_documents(queries[1..3])`, `start_service`, the account tools, `reply_without_data`. Account answers from a code template (no LLM). Shorten the router prompt with examples.
- Tests: all flow rules in `SERVICE_FLOWS.md` section 1, each with a fake router that returns the wrong thing; rules layer; multi-query retrieval merge.
Acceptance: personal and service requests always trigger verification regardless of what the router returns; a three-part document question is answered in full.

### Stage 3: the services
Read: `SERVICE_FLOWS.md` (all), `DOCUMENTS_SPEC.md` sections 3 and 5, `REDESIGN.md` section 10.
- `seed_data/bank/service_catalog.json`; the bank service functions (create request with reference and `flow_id`, guarded card block, list and get requests, mini-statement); the six flows; `seed_bank.py --reset`; the data additions.
- Tests for every flow, every eligibility case (CUST-005, 006, 007, 008), EMI math (about Rs 10,624 for 5 lakh, 10%, 60 months), double confirm, expiry mid-flow, customer scoping of requests.
Acceptance: the ten scripted conversations in `SERVICE_FLOWS.md` section 10.

### Stage 4: refactor and cleanup
Read: `CODE_RULES.md` section 10.
- Behaviour-preserving refactor of the whole codebase under the protocol: remove dead code, unused settings and legacy fields (old owner and user fields, retired phase comments), split long functions (especially the chat run function), one module for fixed messages, consistent names, and comments to the standard in `CODE_RULES.md` section 6 everywhere (module headers, `# Decision:`, minor action comments).
- Report lines of code before and after. Tests stay green at every step.

### Stage 5: documents and retrieval
Read: `DOCUMENTS_SPEC.md`.
- Write the ~20 documents, replace the old ones, make `seed_kb.py` mirror `seed_docs/`, extend the eval cases with `expect_doc`, report retrieval hit rate and groundedness numbers from tests with fakes, and list the live commands for me. No "Lantern" anywhere.
- If you have no web access, write only the fictional documents and list the real-rule ones as to-do with the facts to verify.

### Stage 6: tests, evaluation, README
- Integration tests through the HTTP layer for each route type (document answer, account answer, each flow, expiry, rate limit, customer change).
- Update `eval/` and `scripts/run_eval.py` for the anonymous contract and the new documents.
- Update `README.md`: what it is, architecture picture, setup in PowerShell with uv, demo customers and the verification script, how to seed (`seed_kb`, `seed_bank`, `seed_bank --reset`), how to run, the log format, free-tier limits, limitations.

## Prompt for R2

> Read `AGENTS.md`, `docs/CODE_RULES.md`, `docs/REDESIGN.md` and the R2 section of `docs/PHASES.md`. Implement **R2 stage by stage** in the order given, reading `docs/SERVICE_FLOWS.md`, `docs/DOCUMENTS_SPEC.md` and `docs/LOGGING.md` when a stage names them. Read the real code before changing it and tell me where it differs from the docs. Priorities: correct execution, speed (no LLM call when code can decide) and the least code that works; short simple code over long code. Follow `CODE_RULES.md`: change only what a stage needs, no unrelated edits, search before you read, edit hunks, run narrow tests while working and the full suite once per stage. Comments are required everywhere they belong: a module header, a `# Decision:` comment at every major decision (a feature, a library added or called, a module, a security choice) and short comments on minor code actions that help a reader. Account numbers, mobile numbers and OTPs must never reach a prompt, stored history (store masked) or the logs. Never read my `.env`; add placeholders to `.env.example`. If a stage needs a decision that is not in the docs, ask me. If your context runs low, stop at the end of a stage with the suite green and report. End with the report format in `CODE_RULES.md` section 9, including the exact PowerShell commands I should run and the live checks.

## Manual checks after the run (the human runs these)

```powershell
Set-Location C:\Projects\doctrag
uv run python -m pytest -q
uv run python -m scripts.seed_kb
uv run python -m scripts.seed_bank --reset
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Open `http://127.0.0.1:8000/app/` and run the ten conversations in `docs/SERVICE_FLOWS.md` section 10, a few general document questions (including one with several parts), and one out-of-scope question. Watch the terminal for the events in `docs/LOGGING.md`.

After each run: `git add -A` then `git commit -m "<short, plain description>"`.