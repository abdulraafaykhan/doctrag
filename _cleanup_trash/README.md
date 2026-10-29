# Harbourline Bank assistant

The assistant provides document Q&A, exact rate lookup, session-owned uploads,
in-chat demo OTP authentication, balances and statements, and confirmed cheque
book / debit card requests. FastAPI serves the React UI after a production build.

Install uv and Node 20+ (including npm), then run from this folder in PowerShell:

```powershell
uv venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
Copy-Item .env.example .env
# Set the three credentials in .env locally; never paste them into chat or logs.
.venv\Scripts\python.exe scripts\seed_db.py
.venv\Scripts\python.exe scripts\create_indexes.py
.venv\Scripts\python.exe scripts\make_sample_docs.py
.venv\Scripts\python.exe scripts\ingest_bank_docs.py
npm --prefix frontend ci
.\scripts\run_dev.ps1
```

Open http://127.0.0.1:5173 for development. The Vite proxy forwards `/api` to
port 8000; both processes print to the same terminal and stop with Ctrl+C.
If this desktop runtime exposes Node without npm, `run_dev.ps1` also recognizes
the npm CLI under ignored `data/tools/`; a normal Node installation needs no workaround.

For production-style local serving:

```powershell
npm --prefix frontend run build
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. Only one tab owns a chat; reload uses the same
tab ID, while a second tab receives 409. Cookies must be enabled. Uploads expire
after the configured retention period; expired cards remain visible after reload.
The demo login is mobile `9000000001`, account `000000000001`, then any six digits.
These are synthetic fixtures. See [docs/DEMO.md](docs/DEMO.md) for the six-step demo.

Gate 3 commands (the live command spends free-tier quota only for uncached inputs):

```powershell
.venv\Scripts\python.exe -m pytest tests/test_gate3.py -q
npm --prefix frontend run build
.venv\Scripts\python.exe scripts\run_gate3.py
.venv\Scripts\python.exe -m pytest -q
```

Live cases use the configured Groq pacing guard and retain both successes and
failures in `data/test_cache/`. Existing cached cases are never regenerated.
Customer actions in the evaluation use isolated synthetic records; bank retrieval
reads the already-ingested Atlas corpus. `eval/last_report.md` records limitations.

Use Python 3.12 from `.venv\Scripts\python.exe` for every command. Install the
locked dependencies with `uv pip install --python .venv\Scripts\python.exe -r requirements.txt`.
`pyproject.toml` mirrors `requirements.in`; `requirements.txt` is the lockfile.

Set `GROQ_API_KEY`, `GEMINI_API_KEY`, and `MONGODB_URI` in the local `.env`.
Models, free-tier budgets, and all runtime limits are in `config/settings.yaml`;
confirm provider model availability and budgets in the provider consoles.

```powershell
.venv\Scripts\python.exe scripts\create_indexes.py
.venv\Scripts\python.exe scripts\seed_db.py
.venv\Scripts\python.exe -m uvicorn app.main:app --no-access-log
.venv\Scripts\python.exe -m pytest tests/test_money.py tests/test_masking.py tests/test_config.py tests/test_quota.py
```

The index script waits for the Atlas vector index to be READY. Keyword retrieval
uses the normal `chunks_text` MongoDB text index. The API fails startup when
required indexes are missing. Seeding upserts only the committed fake records;
it does not clear collections. Seed identities and addresses are demo data only.

Logs are masked in the terminal and in rotating JSON files under `data/logs/`.
The tests use fake credentials and an in-memory quota store, with no provider or
database calls. Live provider calls are not part of Phase 1 verification.

Phase 2 samples and Gate 1:

```powershell
.venv\Scripts\python.exe scripts\make_sample_docs.py
.venv\Scripts\python.exe scripts\create_indexes.py
.venv\Scripts\python.exe scripts\ingest_bank_docs.py --defer-embeddings
.venv\Scripts\python.exe -m pytest tests/test_gate1.py -q
$env:BANK_AGENT_LIVE_GATE = '1'
.venv\Scripts\python.exe -m pytest tests/test_gate1_live.py -q -s
```

Sample generation preserves existing files. Ingestion skips unchanged hashes;
incomplete jobs resume with deterministic chunk IDs. The live gate embeds stored
chunks and queries, caches provider responses in `data/test_cache/`, and writes
`eval/gate1_report.json` plus a readable report. Default tests skip the live gate.
Quota counts use the Pacific day and count each embedding text conservatively.
Ingest vision prefers Gemini with a 90-second timeout; online vision prefers
Groq with a 25-second timeout. No input is trusted as an instruction.

Phase 3 conversation engine and Gate 2:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts\run_gate2.py
.venv\Scripts\python.exe scripts\run_gate2.py --release --model groq/qwen/qwen3.8-27b
```

`Chat.turn()` owns verification, held-message replay, leased sessions and explicit
confirmation. Session-bound CrewAI tools expose only the actions permitted in the
current state. The default suite uses FakeLLM; the explicit live runner uses Groq
only for B/G/H, caches requests and complete cases, and paces against the published
free-tier limits plus measured usage. No chat API routes are added before Phase 4.
`scripts/retry_gate1_images.py` retries only the two original provider failures;
successful cached image results and unchanged bank documents are reused.
