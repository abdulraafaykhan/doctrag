# doctrag: Harbourline Bank assistant. Instructions for coding agents

Read in this order: this file, then `docs/CODE_RULES.md`, then `docs/REDESIGN.md`. Read `docs/SERVICE_FLOWS.md`, `docs/DOCUMENTS_SPEC.md` and `docs/LOGGING.md` when the stage in `docs/PHASES.md` says so. `docs/DESIGN.md` is legacy: only its section 10 (prompts), 12 (token budget) and 13 (output guard) still apply.

## What this project is

A chatbot for **Harbourline Bank**, a fictional Mumbai bank. It is a portfolio demo with **fake data only**, run on the developer's machine.

- The chat is **anonymous**. No login page.
- Anyone can ask general bank questions, answered from approved documents (a shared knowledge base) with `[n]` citations.
- A customer is **verified inside the chat** (account number, mobile number, demo OTP) only when they ask for personal data or a service.
- A verified customer can read their own data (balance, transactions, card status), use guided **service flows** (cheque book, block card, new card, mini-statement, request status), and attach files.
- The EMI estimate is public and needs no verification.
- No web access, no answers from the model's general knowledge. Anything else gets a fixed refusal.

## Stack

- Python 3.12 typing (`list[str]`, `X | None`), FastAPI, uv, Windows PowerShell. Root `C:\Projects\doctrag`. Import root is `app`.
- MongoDB Atlas free tier (`AsyncMongoClient`, `$vectorSearch`). Index `chunks_vector_index`: 768 dims, cosine. Bank data lives in a separate database (`BANK_DB_NAME`).
- Groq. Models come from `.env`; the working values are `openai/gpt-oss-120b` (answers) and `openai/gpt-oss-20b` (router). The old Llama ids were retired by Groq. gpt-oss does **not** support parallel tool calls.
- Gemini `gemini-embedding-001`, 768 dimensions, normalised.
- Frontend: React + Vite + TypeScript in `frontend/`, served at `/app/`.
- Not used, do not add: OCR, CrewAI, LangChain agents, web search, a tokenizer library.

## The folder on disk is the source of truth

Docs can lag the code. **Read a file before you change it.** If reality contradicts the docs, say so in your report and follow the code unless it breaks a rule here.

## Non-negotiable rules

### Security
1. **Never read, print, log or commit `.env`** or any secret. New settings go into `.env.example` as placeholders and are listed in your report.
2. **Identity comes only from the server-side verified session** (`session.verified.customer_id`). Never accept a customer, account or user id from a request body, query string, model output or tool argument. Tool schemas shown to the LLM contain no identity fields.
3. Every read or write of customer data filters by the verified customer. Unknown or expired session id: **404**. A request or account that is not the customer's: answer exactly as if it did not exist.
4. **Account numbers, mobile numbers and OTPs are handled by plain code only.** They never go into an LLM prompt, stored history (store masked) or logs (mask to the last 4 digits). See `docs/REDESIGN.md` section 7.
5. **The LLM is untrusted.** Its output, retrieved passages, uploaded files and tool results are data, never instructions. The LLM never decides whether someone is verified, and never writes to the bank database; code does.
6. Never log message text, balances, tokens or passwords. Audit events record events, not content.
7. Money is **integer minor units** plus a currency code, never floats.
8. No web-access code: no search, no fetching of user-supplied URLs.

### Engineering
9. Follow `docs/CODE_RULES.md` on every task. It governs scope, comments, simplicity and token use.
10. Work **stage by stage** as `docs/PHASES.md` says. After each stage the test suite is green. Never leave it red.
11. Keep the uv workflow: `uv add`, `uv remove`, then `uv export --no-hashes --no-dev -o requirements.txt`. `pyproject.toml` keeps `[tool.uv] package = false` and the pytest `pythonpath` setting.
12. Tests: pytest with fake Mongo and fake LLMs. Live services (Groq, Gemini, Atlas) cannot be tested from here, so list the exact manual check for anything that touches them.
13. Pydantic models for every request and response. Swagger stays usable.
14. Free-tier budget: do not add LLM calls beyond those in `docs/REDESIGN.md`. Most turns should need zero or one.
15. Commands you give the human are **PowerShell**, run from `C:\Projects\doctrag`.
16. If a decision is not covered, **ask**. Never guess on anything security-related.

## Gotchas already known

- `app/main.py` contains `_fix_binary`, an OpenAPI patch for Swagger's file picker. Keep it.
- `$vectorSearch` filters only on fields declared in the Atlas index definition.
- Run tests with `uv run python -m pytest -q` (or `uv run pytest -q`; `pythonpath` is set).
- Python scripts run from the project root (`uv run python -m scripts.<name>`), never from `frontend/`.
- Embeddings are batched (50 per call) with retry on 429, 500 and 503. Keep that.
- Documents stuck in `processing` after a restart are swept to `failed` at startup. Keep that.

## What you may run yourself

`uv add`, `uv remove`, `uv sync`, `uv export ...`, `uv run python -m pytest ...`, `uv run python -m scripts.generate_bank_data`, and `npm install`, `npm test`, `npm run build` inside `frontend/`.

Do not run anything that needs real keys or the live database (`seed_kb`, `seed_bank`, `migrate_*`, `run_eval`, `uvicorn` against Atlas). Give the exact PowerShell commands instead.

Do not run `git commit` or `git push`. Never open or edit `.env`.

## Definition of done for a stage

- The stage's acceptance checks in `docs/PHASES.md` pass (the ones you can run).
- `uv run python -m pytest -q` and the frontend tests are green.
- `requirements.txt` and `.env.example` are updated if dependencies or settings changed.
- A short report (see `docs/CODE_RULES.md` section 9) ends with the exact PowerShell commands the human should run.