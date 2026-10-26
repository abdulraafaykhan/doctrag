# Harbourline Bank assistant

The foundation provides configuration, masked logging, MongoDB indexes and demo
data, provider clients with quotas, money helpers, and `GET /api/health`. Phase 2
adds shared ingestion, local parsers, OCR/vision checks, embeddings, hybrid search,
exact rate lookup, and session-owned upload processing and cleanup.

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
