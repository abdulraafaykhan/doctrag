# Harbourline Bank assistant

Phase 1 provides configuration, masked logging, MongoDB indexes and demo data,
provider clients with quotas, money helpers, and `GET /api/health`.

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
