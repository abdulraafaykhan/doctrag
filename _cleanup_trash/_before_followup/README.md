# Harbourline Bank assistant

A fictional Mumbai bank demo: FastAPI, React/Vite, MongoDB Atlas, local OCR,
document retrieval, demo OTP login, and code-confirmed banking request flows.
The current agent and vision models use the local server; embeddings still use Gemini.
Local mode exposes a smaller service set than the Groq/CrewAI implementation.
See [architecture](docs/ARCHITECTURE.md), [database map](docs/DB_MAP.md), and
[local model settings and provider switch](docs/LOCAL_LLM.md).

## Setup (PowerShell)

Work in `C:\Projects\bank_agent` on branch `local-llm`; never modify the separate doctrag project.
Install uv and Node 20+ with npm. Use Python 3.12 exclusively through this project's venv.

```powershell
uv venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
# Only for a fresh setup when .env does not already exist:
Copy-Item .env.example .env
npm --prefix frontend ci
```

Fill the local `.env` yourself: `GROQ_API_KEY`, `GEMINI_API_KEY`, `MONGODB_URI`,
and `LOCAL_LLM_URL` for local mode. Keys remain required by settings even when
the corresponding chat provider is disabled. `LOG_LEVEL` is optional.
The database name is `database.name` in `config/settings.yaml`; `MONGODB_DB` in
the example environment file is not a Secrets field and does not override it.
Allow your IP in Atlas; use this app's database, never another project's data.
Model names, limits and wording live in `config/`; prompts live in `prompts/`.
Settings are cached: restart after editing configuration or environment variables.

```powershell
.venv\Scripts\python.exe scripts\create_indexes.py
.venv\Scripts\python.exe scripts\seed_db.py
.venv\Scripts\python.exe scripts\make_sample_docs.py
.venv\Scripts\python.exe scripts\ingest_bank_docs.py
```

Index creation waits for the single Atlas vector index to be READY; keyword search
uses a normal MongoDB text index. It does not remove unrelated search indexes.
Seeding upserts the fictional records without clearing collections. Sample generation
preserves existing files. Ingestion deduplicates file hashes and resumes unfinished jobs.
`--defer-embeddings` on the ingestion script postpones vector generation; normal ingestion
and image processing can spend provider quota. Do not run them when live calls are prohibited.
`requirements.txt` is the installation lock; `pyproject.toml` mirrors `requirements.in`.
Always install with `uv pip install --python .venv\Scripts\python.exe ...`, never bare pip.

## Run

One terminal:

```powershell
.\scripts\run_dev.ps1
```

Open http://127.0.0.1:5173. The launcher reads API settings from `config/ui.json`,
starts the backend plus Vite, and forwards Ctrl+C for graceful backend shutdown.
`scripts/dev_backend.py` is required by this launcher. The launcher can use the
ignored npm CLI in `data/tools/` when npm is not on PATH.

Or use two terminals from the project root:

```powershell
# Terminal 1
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
# Terminal 2
npm --prefix frontend run dev
```

For a built UI served by FastAPI:

```powershell
.\scripts\run_dev.ps1 -Build
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Open http://127.0.0.1:8000 for the built UI. Startup checks Atlas and index readiness;
`/api/health` reports availability. One client tab owns each chat; a second tab receives 409.
Heartbeat refreshes tab presence, not activity. After 15 idle minutes the chat and
owned uploads expire; the UI starts fresh. Sent messages appear immediately and queue
while a reply is pending; New chat aborts the browser request and clears its queue.
Logs are masked, with trace trees and token totals; rotating JSON lives in `data/logs/app.log`.

## Inspect stored data (read only)

```powershell
.venv\Scripts\python.exe scripts\inspect_db.py summary
.venv\Scripts\python.exe scripts\inspect_db.py session
.venv\Scripts\python.exe scripts\inspect_db.py session <session-id>
.venv\Scripts\python.exe scripts\inspect_db.py docs
.venv\Scripts\python.exe scripts\inspect_db.py chunks "interest_rates.pdf"
.venv\Scripts\python.exe scripts\inspect_db.py extraction <doc-id>
.venv\Scripts\python.exe scripts\inspect_db.py drop-unknown
.venv\Scripts\python.exe scripts\clean_test_data.py
```

`drop-unknown` lists unknown collections; do not add `--yes` unless their removal is intended.
`clean_test_data.py` previews all sessions, pending actions, audit events and requests,
plus user-namespace knowledge rows. Its optional `--yes` deletes those rows only;
it does not delete upload files, fixtures, quotas or bank content. Stop the app before
an intentional deletion so its worker does not race the operation. No deletion was
performed during repository cleanup.

## Six-step demo

All names, documents and amounts below are fictional. Start one New chat. Wait for
attachment cards to become Ready or Partial; Partial means review may be needed.
These are the original demo's expected outcomes, not a claim that the current local
model completes every step. The last local screenshot search had no good match;
the writer path has not yet been validated end to end after its logging fix.

1. **Rate-table upload.** Attach `data/gate1_images/rates_00.png`.
   Type: **From my attached image, what are the general and senior rates for 12 months?**
   Expected fixture rates: **6.50% general, 7.00% senior**, cited as your file.
   The sent image remains openable; questionable cells must be flagged, never guessed.
   These fixture rates differ from the bank's current rate card.
2. **PDF question.** No attachment; bank knowledge already contains `loan_eligibility.pdf`.
   Type: **What are the age limits for a home loan applicant? Cite the document.**
   Expected: **21 to 65**, with Loan eligibility, page and version in Sources.
   Alternatively attach `data/bank_docs/loan_eligibility.pdf` and type:
   **According to my PDF, what are the age limits for a home loan applicant?**
   That answer must cite your file.
3. **Current exact rate.** No attachment.
   Type: **What is the fixed deposit rate for 12 months for a general customer?**
   Expected corpus value: **7.10%**, effective **2026-10-01**, from the current card,
   never the superseded card or the uploaded fixture. Local mode searches sources;
   it does not expose the separate exact `lookup_rate` tool by default.
4. **OTP and balance.** No attachment.
   Type: **Show my balance. Mobile 9000000001, account 000000000001.**
   Expected: ask for the demo OTP without revealing a balance. Type **123456**.
   Expected after verification: **₹57,600.00**, masked account ending **0001**,
   and a verified login chip. Typed demo OTP text may remain in the UI transcript;
   it is excluded from model history. Six-digit document references are not masked.
5. **Cheque book.** Type: **I need a new chequebook**.
   Click **40 pages**, **Emergency (within 1 week)**, then **Address on file**.
   Expected: sent bubbles for clicks; a summary with Confirm / Change / Cancel.
   Click **Confirm**. Expected: exactly one request, **CB-…** reference and expected date;
   repeating Confirm must not duplicate it. No submission before confirmation.
6. **Multi-part request.** Remain verified; no attachment.
   Type: **Show my balance, last 5 transactions and the current 12-month general FD rate.**
   Full-service expected answer: **₹57,600.00**, five newest-first transactions with
   code-formatted amounts, and **7.10%** from the current rate card, without re-login
   or a full account number. Current local routing returns the balance display for
   this message; statements are disabled in its tool list, so the complete answer is
   a known local-mode limitation. The Groq configuration restores the full tool set.

Existing evaluation reports remain in `eval/`; incomplete or quota-blocked cases are
not passing results. A safe inability-to-answer is a limitation, not demo success.

## Reversible repository cleanup

Archived files retain relative paths under `_cleanup_trash/`; nothing was deleted.
Its manifest records source hashes. Tests and some evaluation helpers remain because
the initial README/CLAUDE references triggered the requested ASK rule. Old build prompts
and test specifications remain historical material; they do not override CLAUDE's Git rules.
