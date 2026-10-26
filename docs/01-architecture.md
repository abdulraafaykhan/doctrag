# 01 Architecture

## Components
| Part | Tech | Where it runs |
|---|---|---|
| Web API | FastAPI + uvicorn | local |
| Agent | CrewAI (`Agent`, `Task`, `Crew`), one agent built per turn | local, calls Groq |
| Guardrails, auth, flows, validators, number check | plain Python | local |
| LLM (answers, tool choice) | Groq, model names in `config/settings.yaml` | API, free tier |
| Vision (hard images only) | Gemini Flash via `google-genai` | API, free tier |
| Embeddings | `gemini-embedding-001`, 768 dims, normalized | API, free tier |
| OCR | RapidOCR (`rapidocr-onnxruntime`) | local CPU |
| Parsing | pymupdf, pdfplumber, python-pptx, python-docx, openpyxl, pandas | local |
| Reranker (optional, off by default) | cross-encoder, only if `features.reranker: true` | local |
| Store | MongoDB Atlas M0 via `pymongo` | cloud, free |
| Files | `data/uploads/`, `data/crops/` (24 h cleanup) | local disk |
| Frontend | React + Vite | browser |

## One chat turn (online path)
1. `POST /api/chat` (message, optional attachments). Load or create the session.
2. **State shortcuts, no LLM:** if awaiting OTP and the message is 6 digits, verify it. If a flow is awaiting confirmation, handle confirm / change / cancel in code.
3. **Pre-pass (code):** regex extract mobile (10 digits, starts 6-9, allow +91 and spaces), account (12 digits, allow spaces or dashes), a 6-digit code, commands (cancel, back). Mask them out of the text sent to any model.
4. **Attachments:** run the image/file cascade (docs/05). Images are read before login; results are cached by hash and held for later.
5. **Login check (code):** build the tool set for this turn from `auth.state` and `active_flow` (docs/03). If a personal need is detected by the agent via `start_authentication`, code holds the message and drives login (docs/04).
6. **Agent (CrewAI + Groq):** gets masked text, history (12 turns, masked), the system prompt (`prompts/agent_system.txt`) with injected blocks, and only the allowed tools. Limits: 4 steps, 6 tool calls.
7. **Guardrails (code, inside every tool):** bind the verified user, validate arguments with pydantic, enforce flow rules, write `audit_log`.
8. **Answer:** the agent's final message. Tool results carry a `display` string built by code (statement table, balance line, summary, OTP prompt); the agent must include it unchanged.
9. **Verify (code):** every number in the reply must appear in tool output or retrieved sources (treat 8.5 and 8.50% as equal). If not: one repair call (`prompts/repair.txt`), then the fixed safe message from `config/messages.yaml`.
10. **Respond** with text, citations (`[n]` plus a source list), the open flow's button options, and attachment records. Persist session. Log every step.

There is no separate router call, rewrite call or composer call. The tool set is the router; the agent writes self-contained search queries itself.

## Offline path (ingestion)
`scripts/ingest_bank_docs.py` and the upload handler use the same pipeline: validate, hash, queue, parse, image cascade, chunk, embed, store, report. Resumable through `ingest_jobs`. See docs/05.

## Model fallback chain
Groq agent model, then a second Groq model, then Gemini Flash text, then a friendly "please retry" message. On HTTP 429 or timeout the turn is retried once on the next model. Log every fallback. Vision: Gemini Flash only; if out of budget, return local OCR text with an explicit low-confidence warning.

## Quotas (free tier)
`quota_usage` (one doc per Pacific day) counts embedding, VLM and LLM requests. A guard function is called before every external call. At 70 percent of the embedding budget, uploads wait; bank Q&A keeps working. Numbers live in config.

## Concurrency
One chat per browser tab group. A second tab is blocked (frontend `BroadcastChannel`/localStorage lock; backend rejects a second active client id per session with a clear message). Background ingestion runs in one worker thread started at app startup.

## Error posture
Fail safe and say so. Never invent a fact or number. When a dependency is down, reply with the fixed message and keep session state consistent. No partial writes: a request is created in one atomic operation guarded by a unique `idempotency_key`.
