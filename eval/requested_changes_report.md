# Requested changes — 28 October 2026

All six items were implemented in order and checked separately. No live LLM, VLM or embedding requests were made; provider interfaces used FakeLLM or stub SDK/HTTP responses. No staging, commits, pushes or other Git mutations were performed. No files under doctrag were accessed.

## Results

| Item | Result | Evidence |
|---|---|---|
| 1 — Tokens | PASS | Two FakeLLM calls: in=0, out=0, total=0, calls=2, cached=True. Two stub HTTP responses: in=18, out=5, total=23; persisted session totals match. Gemini output includes reported thought tokens. Concurrent additions are exact. API request/session totals and reload restoration pass, including a cookie-only chat request. |
| 2 — Traces | PASS | Both HTTP trace trees are pasted below; JSON span records have trace/parent IDs, real module/function, timings, status and attributes. DB operations remain DEBUG and are aggregated. |
| 3 — Masking | PASS | Ref 482913 and typed demo OTP survive; OTP verification bypasses the model and its code stays out of model history. Mobile/account/card values remain masked. PIN/CVV/password labels stay masked. |
| 4 — Idle deletion | PASS | idle_minutes=0.05 with an aged clock: all owned collections, upload/crop files and owner caches disappear; audit remains. Heartbeat does not extend activity. Chat, upload and heartbeat return 410/SESSION_EXPIRED; POST session creates a fresh session. |
| 5 — UI queue | PASS | Installed headless Edge: typed message and option click queue immediately behind a slow reply, then complete FIFO. An upload starts during thinking and joins the next queued message. New chat aborts/discards old work. Heartbeat updates tokens; expiry clears chat/tokens. Composer fits at 360px/1280px without overlap or horizontal overflow. Concurrent upload/token updates survive chat persistence. |
| 6 — Storage/provenance | PASS | Cached sample image-PDF ingestion, unchanged-file skip, readable image chunks, provenance links, vector reuse and migration preservation pass. Inspector output for a table and a slip is pasted below. |

The selected offline regression run passed **59 cases, 0 failed, 58 deselected**. Subsequent focused checks passed as well, including the additional API/reload case: **60 distinct pytest cases passed across these targeted runs**. These are not full-suite or live-quality results. The browser check passed six assertion groups and the frontend build passed.

Earlier check failures were resolved: an occupied fixture port was replaced by an ephemeral port; the UI answer assertion now targets the answer paragraph rather than option text; a native PDF heading correctly has no image extraction ID; missing attachment arrays in restored UI history are handled; history retains masked-credential messages; and tests for the deliberately changed expiry/span contracts were updated. No failing case remains in the selected checks.

## Graceful shutdown

The actual Node dev launcher started an offline FastAPI fixture, received its Ctrl+C/SIGINT path, waited for uvicorn, and produced:

```text
INFO:     Shutting down
INFO:     Waiting for application shutdown.
PROCESS TOKENS | in=0 out=0 total=0 calls=0
INFO:     Application shutdown complete.
INFO:     Finished server process
```

A separate lifespan check with stub usage printed SESSION TOKENS and PROCESS TOKENS with total=10. The process ledger retains accounting after idle deletion. Windows forwarding uses the child stdin control pipe to invoke uvicorn's SIGINT handler; it does not hard-kill the backend. The check exercises that launcher path without a live provider or production startup.

## Database migration

Applied only to the configured `harbourline` database: 34 image_cache rows preserved in extractions, 21 job rows migrated into documents, reusable embedding-cache vectors preserved in chunks before removing embed_cache. Existing Atlas search indexes were unchanged. No unknown collection was dropped. The read-only summary showed exactly the target 15 collections: users 10, accounts 10, cards 10, transactions 480, requests 6, sessions 15, pending_actions 0, audit_log 67, documents 21, sections 120, chunks 236, rate_tables 10, rate_rows 52, extractions 34, quota_usage 2. Counts are the migration-time snapshot.

Existing extraction rows retain empty new provenance fields, as requested. The table/slip inspection below comes from the **offline MemoryStore fixture**, not a new Atlas model run: the real sample rates_image.pdf is parsed, FakeLLM primes a cache, and ingestion reuses that cached extraction with no further model call. One rate digit and one slip amount were deliberately wrong to prove nulling/review provenance. This is a code-path check, not a claim about real OCR/VLM accuracy. `scripts/inspect_db.py extraction <name-or-id>` is the same inspector function used for the pasted output.

## Actual trace trees

The following are captured HTTP requests for `hi` and `I need a new chequebook`, using FakeLLM. Zero DB operations reflect the injected MemoryStore; a separate Store test verifies DEBUG-only DB spans and aggregation.

```text
TRACE TREE r-de21  /api/chat total=0.01s db=0.000s (0 ops)
├─ PREPASS app.prepass.extract ok 0.000s name=extract 
├─ PREPASS app.prepass.extract ok 0.000s name=extract 
└─ RESPONSE app.api.API.turn_locked ok 0.000s name=response chars=44
TRACE TREE r-9afb  /api/chat total=0.02s db=0.000s (0 ops)
├─ PREPASS app.prepass.extract ok 0.000s name=extract 
├─ AGENT app.agent.Runner.run ok 0.010s name=agent turn in=0 out=0 total=0
│  ├─ LLM app.agent.Runner._fake ok 0.002s name=scripted request model=scripted in=0 out=0 total=0
│  ├─ TOOL app.tools.registry.BoundTool.invoke ok 0.001s name=chequebook_set_slots 
│  └─ VERIFY app.verify.check ok 0.000s name=number check 
├─ PREPASS app.prepass.extract ok 0.000s name=extract 
└─ RESPONSE app.api.API.turn_locked ok 0.000s name=response chars=50
```

## Actual extraction inspector output

```text
rates_image.pdf | page=1 | ext_e95e887b96e3117fadfed226
OCR: Tenure months
General rate
Senior rate
Effective from
12
7.10%
7.60%
01 Oct 2026
24
7.35%*
7.85%
01 Oct 2026
36
6.85%
7.35%
01 Oct 2026
VLM: {"columns": ["Tenure months", "General rate", "Senior rate", "Effective from"], "rows": [["12", "7.10%", "7.61%", "01 Oct 2026"], 
["24", "7.35%*", "7.85%", "01 Oct 2026"], ["36", "6.85%", "7.35%", "01 Oct 2026"]], "overall_confidence": 0.99}
                                 Comparison                                  
┌────────────────────────────┬─────────────┬─────────────┬───────┬──────────┐
│ Field                      │ OCR         │ VLM         │ Match │ Selected │
├────────────────────────────┼─────────────┼─────────────┼───────┼──────────┤
│ row 1 / col Tenure months  │ 12          │ 12          │ True  │ vlm      │
├────────────────────────────┼─────────────┼─────────────┼───────┼──────────┤
│ row 1 / col General rate   │ 7.10%       │ 7.10%       │ True  │ vlm      │
├────────────────────────────┼─────────────┼─────────────┼───────┼──────────┤
│ row 1 / col Senior rate    │ 7.60%       │ 7.61%       │ False │ none     │
├────────────────────────────┼─────────────┼─────────────┼───────┼──────────┤
│ row 1 / col Effective from │ 01 Oct 2026 │ 01 Oct 2026 │ True  │ vlm      │
├────────────────────────────┼─────────────┼─────────────┼───────┼──────────┤
│ row 2 / col Tenure months  │ 24          │ 24          │ True  │ vlm      │
├────────────────────────────┼─────────────┼─────────────┼───────┼──────────┤
│ row 2 / col General rate   │ 7.35%*      │ 7.35%*      │ True  │ vlm      │
├────────────────────────────┼─────────────┼─────────────┼───────┼──────────┤
│ row 2 / col Senior rate    │ 7.85%       │ 7.85%       │ True  │ vlm      │
├────────────────────────────┼─────────────┼─────────────┼───────┼──────────┤
│ row 2 / col Effective from │ 01 Oct 2026 │ 01 Oct 2026 │ True  │ vlm      │
├────────────────────────────┼─────────────┼─────────────┼───────┼──────────┤
│ row 3 / col Tenure months  │ 36          │ 36          │ True  │ vlm      │
├────────────────────────────┼─────────────┼─────────────┼───────┼──────────┤
│ row 3 / col General rate   │ 6.85%       │ 6.85%       │ True  │ vlm      │
├────────────────────────────┼─────────────┼─────────────┼───────┼──────────┤
│ row 3 / col Senior rate    │ 7.35%       │ 7.35%       │ True  │ vlm      │
├────────────────────────────┼─────────────┼─────────────┼───────┼──────────┤
│ row 3 / col Effective from │ 01 Oct 2026 │ 01 Oct 2026 │ True  │ vlm      │
└────────────────────────────┴─────────────┴─────────────┴───────┴──────────┘
selected_source=vlm | VLM selected; OCR validation flags retained; fields left null after OCR/geometry checks: row 1 / col Senior rate
offline-slip.png | page=1 | ext_66009808ec51442dbcfe0745
OCR: Ref: 482913
Amount: 1250
VLM: {"fields": {"Ref": {"value": "482913"}, "Amount": {"value": "1258"}}, "overall_confidence": 0.99}
                  Comparison                   
┌────────┬────────┬────────┬───────┬──────────┐
│ Field  │ OCR    │ VLM    │ Match │ Selected │
├────────┼────────┼────────┼───────┼──────────┤
│ Ref    │ 482913 │ 482913 │ True  │ vlm      │
├────────┼────────┼────────┼───────┼──────────┤
│ Amount │ 1250   │ 1258   │ False │ none     │
└────────┴────────┴────────┴───────┴──────────┘
selected_source=vlm | VLM selected; OCR validation flags retained; fields left null after OCR/geometry checks: Amount
```

## Changed files

- [CLAUDE.md](C:/Projects/bank_agent/CLAUDE.md)
- [app/agent.py](C:/Projects/bank_agent/app/agent.py)
- [app/api.py](C:/Projects/bank_agent/app/api.py)
- [app/chat.py](C:/Projects/bank_agent/app/chat.py)
- [app/db.py](C:/Projects/bank_agent/app/db.py)
- [app/ingest/chunking.py](C:/Projects/bank_agent/app/ingest/chunking.py)
- [app/ingest/embeddings.py](C:/Projects/bank_agent/app/ingest/embeddings.py)
- [app/ingest/images.py](C:/Projects/bank_agent/app/ingest/images.py)
- [app/ingest/models.py](C:/Projects/bank_agent/app/ingest/models.py)
- [app/ingest/pipeline.py](C:/Projects/bank_agent/app/ingest/pipeline.py)
- [app/llm.py](C:/Projects/bank_agent/app/llm.py)
- [app/log.py](C:/Projects/bank_agent/app/log.py)
- [app/main.py](C:/Projects/bank_agent/app/main.py)
- [app/prepass.py](C:/Projects/bank_agent/app/prepass.py)
- [app/rag.py](C:/Projects/bank_agent/app/rag.py)
- [app/session.py](C:/Projects/bank_agent/app/session.py)
- [app/uploads.py](C:/Projects/bank_agent/app/uploads.py)
- [app/usage.py](C:/Projects/bank_agent/app/usage.py)
- [app/vision.py](C:/Projects/bank_agent/app/vision.py)
- [config/settings.yaml](C:/Projects/bank_agent/config/settings.yaml)
- [config/ui.json](C:/Projects/bank_agent/config/ui.json)
- [docs/02-data-model.md](C:/Projects/bank_agent/docs/02-data-model.md)
- [docs/04-auth-and-flows.md](C:/Projects/bank_agent/docs/04-auth-and-flows.md)
- [docs/05-rag-and-parsing.md](C:/Projects/bank_agent/docs/05-rag-and-parsing.md)
- [docs/06-frontend-and-logging.md](C:/Projects/bank_agent/docs/06-frontend-and-logging.md)
- [docs/07-tests.md](C:/Projects/bank_agent/docs/07-tests.md)
- [docs/DB_MAP.md](C:/Projects/bank_agent/docs/DB_MAP.md)
- [eval/requested_changes_report.md](C:/Projects/bank_agent/eval/requested_changes_report.md)
- [frontend/src/api.js](C:/Projects/bank_agent/frontend/src/api.js)
- [frontend/src/components.jsx](C:/Projects/bank_agent/frontend/src/components.jsx)
- [frontend/src/main.jsx](C:/Projects/bank_agent/frontend/src/main.jsx)
- [scripts/check_queue.cjs](C:/Projects/bank_agent/scripts/check_queue.cjs)
- [scripts/dev.mjs](C:/Projects/bank_agent/scripts/dev.mjs)
- [scripts/dev_backend.py](C:/Projects/bank_agent/scripts/dev_backend.py)
- [scripts/inspect_db.py](C:/Projects/bank_agent/scripts/inspect_db.py)
- [scripts/migrate_storage.py](C:/Projects/bank_agent/scripts/migrate_storage.py)
- [scripts/retry_gate1_images.py](C:/Projects/bank_agent/scripts/retry_gate1_images.py)
- [scripts/run_gate3.py](C:/Projects/bank_agent/scripts/run_gate3.py)
- [tests/memory_store.py](C:/Projects/bank_agent/tests/memory_store.py)
- [tests/test_gate1.py](C:/Projects/bank_agent/tests/test_gate1.py)
- [tests/test_gate2.py](C:/Projects/bank_agent/tests/test_gate2.py)
- [tests/test_gate3.py](C:/Projects/bank_agent/tests/test_gate3.py)
- [tests/test_masking.py](C:/Projects/bank_agent/tests/test_masking.py)
- [tests/test_requested_changes.py](C:/Projects/bank_agent/tests/test_requested_changes.py)
