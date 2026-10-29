# Implemented architecture

This describes the code on branch `local-llm`, not the original build plan.
Settings are current defaults, not guarantees of provider availability or extraction accuracy.
Related documents: [DB_MAP.md](DB_MAP.md), [LOCAL_LLM.md](LOCAL_LLM.md), [README](../README.md).

## Components and entry points

| Component | Implementation | Execution |
|---|---|---|
| HTTP and static UI | app.main.create_app, app.api.API; FastAPI/uvicorn | Local |
| Conversation | app.chat.Chat._turn; app.agent.Runner.run | Local |
| Local text server | app.local_llm.LocalClient | Configured HTTP endpoint |
| Remote agent option | Runner._native; CrewAI Agent, Task, Crew | Groq/Gemini APIs |
| Identity and service requests | app.session.Sessions; app.flows.Flows | Plain Python |
| Tools | app.tools.registry.Registry, BoundTool | Session-bound Python |
| Retrieval | app.rag.search_knowledge, lookup_rate, rrf | MongoDB Atlas |
| Storage | app.db shared MongoClient; app.ingest.store.Store | Atlas harbourline database |
| Parsing | app.ingest.parsers.parse_file | Local parser libraries |
| Image cascade | app.ingest.images.ImageCascade; app.vision helpers | RapidOCR and configured vision |
| Embeddings | app.ingest.embeddings.Embeddings; app.llm.LLMClient.embed | Gemini |
| Uploads | app.uploads.UploadService, UploadWorker | Disk, DB, background thread |
| UI | frontend/src/main.jsx and components.jsx | React 19/Vite, markdown/GFM, plain CSS |
| Logs and usage | app.log.AppLog; app.usage.TokenMeter | Rich console, rotating JSON, memory/DB |

get_settings in app/config.py loads YAML, JSON UI wording, prompts and Secrets once.
Groq/Gemini credentials remain required by Secrets even in local mode.
The current agent, ingest vision and online vision models are local/default with empty
fallback lists. The embedding model remains gemini-embedding-001, 768 dimensions.
The optional cross-encoder reranker and Docling feature flags are off; parsing does not use Docling.

## A user request

1. POST /api/chat enters API.turn and token_scope. API.owned checks the cookie, client tab,
   idle/absolute expiry and updates user activity. Deleted/expired sessions produce 410 SESSION_EXPIRED.
2. A session lease covers processing and UI transcript persistence. Concurrent chat gets 409 busy.
   Uploads have their own processing path and do not require that chat lease.
3. Chat._turn calls prepass.extract for mobile, account, six-digit code, commands and postal PIN.
   Exact configured greetings/thanks/goodbye and forbidden requests finish without a model.
4. Current attachments must belong to this session. Files not Ready/Partial are processed
   through UploadService; API normally prevents sending attachments still processing.
5. Local-mode routing may execute private search, public search or verified balance in code.
   Identity-bearing messages and an awaiting-OTP code remain on the auth path. See below.
6. Other turns use code-owned auth/flow shortcuts, then Chat._agent and Runner.run.
   Auth-required requests are held; public questions can be answered while OTP is pending.
7. Registry.visible computes the allowed tools; local mode intersects local_llm.enabled_tools.
   Tool schemas reject extra/invalid arguments. The session supplies identity, never model IDs.
8. Runner uses bounded masked history and system_prompt, assembled from agent_system and
   agent_blocks with auth, flow, document, date and bank information. Local scope text is appended.
9. Tool displays remain exact. canonical_citations/check verify numeric evidence and citation IDs;
   a single repair call is allowed, followed by a safe configured message if still invalid.
10. Chat saves history/state; API persists ui_history in the same session row and returns
    reply_markdown, citations, options, auth/flow state, attachments, notices, tools_called and tokens.

### Local code routing

Only models.agent beginning `local/` activates these branches in Chat._turn:
- An attachment this turn, or a configured reference word plus existing session uploads,
  selects search_my_documents with the customer's masked message as query.
- Otherwise public-question signals, non-personal intent, no active flow and no configured
  auth/escalation passthrough word select search_bank_knowledge with that message.
- Otherwise verified balance words select get_balance, if it is visible, and return its display.
- Cheque book, authentication, escalation and flow-side questions otherwise retain the tool loop.

The code-selected tools still use BoundTool.invoke: validation, TOOL span, envelope and audit.
Search items receive the existing `[SOURCE n | label | title | page | effective]` wrapper.
If no_good_match or no items are returned, no writer runs: use answers.not_found and offer a human.
Successful searches use LocalClient.write: no tool list or JSON protocol, just system rules,
bounded history, wrapped retrieved passages, question and prompts/local_writer.txt.
Configured dialogue stop markers cut trailing invented conversation. Normal verification/repair follows.
Errors log the actual reason and return answers.retry_later. Empty agent fallbacks prohibit remote escape.

The original Groq/Gemini path remains a CrewAI tool-selecting agent. It creates one agent per
turn, no delegation/memory/cache, temperature from settings and configured step/tool limits.
The Groq SDK transport uses the OpenAI-compatible endpoint while retaining qwen/ or openai/
inside the native model ID. Gemini uses CrewAI's native implementation, not LiteLLM.
Only configured fallback entries can be tried; local mode currently has none.

## Tool contract and visibility

Each tool returns `{ok, code, data, hint, display}`. Code owns wording and money formatting.
BoundTool.invoke checks visibility, call/retry budgets and Pydantic args; invokes its callback;
records results and masked audit data; wraps search sources and returns a masked envelope.
Errors include INVALID_ARGUMENT, NOT_VERIFIED, NO_DOCUMENTS, UPSTREAM_ERROR and flow/auth codes.

| Auth/flow | Base visible tools before local filtering |
|---|---|
| Anonymous/awaiting OTP | Both searches, list/delete my documents, lookup_rate, emi_calculator, start_authentication, escalate_to_human |
| Verified, no flow | Public tools minus start_authentication; balance, statement, card info, cheque/debit set_slots |
| Collecting | Both searches, flow_status/cancel, active flow's set_slots, escalate; emi_calculator, lookup_rate, flow_summary |
| Awaiting confirmation | Both searches, flow_status/cancel, active flow's set_slots, escalate |

Local enabled tools currently retain searches, start_authentication, get_balance, cheque set_slots,
flow_status/summary/cancel and escalation. Upload references hide public bank search for that turn.
Hidden tools cannot be invoked; changing local enabled_tools restores only state-visible actions.

| Tool | Arguments and result |
|---|---|
| search_bank_knowledge | query 3–300 chars; optional doc_type, product, top_k; active bank passages and no_good_match |
| search_my_documents | query; optional doc_id/top_k; only this session's user documents |
| list_my_documents | No args; session file statuses/pages/warnings |
| delete_my_documents | Exactly one of doc_id or all=true; only session-owned data/files |
| lookup_rate | product, optional tenure_months, amount_rupees, segment, as_of; exact active rate rows or missing dimensions |
| emi_calculator | principal_rupees, annual_rate_percent, tenure_months; code-calculated EMI/interest/payable |
| start_authentication | reason enum; candidates already captured by prepass; missing details/OTP/lock display |
| escalate_to_human | reason; records handoff request and reference |
| get_balance | No args; verified balance, masked account, status, timestamp |
| get_statement | period last_5/last_10/last_30_days/custom; dates, direction, page; markdown rows/totals |
| get_card_info | Optional card_id, filtered to verified identity; last4/status/network/type/expiry/limit |
| chequebook_set_slots | Optional pages, delivery, address; code validates supplied slots |
| debit_card_set_slots | Optional card_type, address; code validates supplied slots |
| flow_status/summary/cancel | Current flow next step; pending summary; cancellation with optional reason |

Search top_k defaults to 5, max 6. Statement custom dates must be ordered, not future and within
90 days; page size is 50. EMI limits and product/segment enums come from configuration.
Cheque pages are 20/40/60; delivery emergency/normal; card type standard/premium.
Tool arguments never accept user_id/account_id/account_number from a model.
OTP sending/verification, confirmation, submission, session I/O, upload cleanup and quota reservation
are code-only operations, not model tools. The current limits are four steps and six tool calls,
with one invalid-argument retry; all limits remain configurable.

## Authentication and flows

Sessions.auth.state is ANONYMOUS → AWAITING_OTP → VERIFIED.
Sessions.start collects missing mobile/account candidates and checks the pair without revealing
which value failed. Failed checks/OTPs lock after three attempts for ten minutes by default.
Resends cap at three. verify_otp checks six digits and five-minute expiry; demo mode accepts any
six-digit code. Only a hash is stored in auth. Held masked requests (up to three) replay after login.
Code captures identity from a single message, preserving held attachment IDs; users need not re-upload.
Different-account input clears verified auth before requesting that account's information.
Cancel while awaiting OTP clears candidates/held messages. Public questions preserve pending auth.

The UI transcript may preserve typed demo codes, but model history excludes detected codes.
Document/reference numbers of six digits are not generally redacted. Labelled PIN/CVV/password
values remain masked. Postal PINs are validated delivery data, separate from card PINs.

Flows uses config/flows/*.yaml for slot order, synonyms, validators, options, summaries and timing.
One active flow per session; state is COLLECTING or AWAITING_CONFIRM. Code fast paths handle
exact options and confirm/change/cancel; the model can suggest slots but cannot submit.
flow_summary creates a 15-minute pending_actions row with locked slots and a pending token.
Only explicit configured confirmation submits. Ambiguous replies retain the summary; side
questions preserve it and repeat the next step. Change unlocks the selected slot; a different
flow is refused until the first is finished/cancelled. Flow idle expiry is configurable.
Flows.submit validates pending owner/account/status/expiry and unchanged slots, rejects frozen
account requests, and uses an atomic $setOnInsert with unique idempotency_key. Repeated confirm
does not duplicate requests. References and expected dates are code-generated; an ambiguous DB
failure checks whether the request already exists before answering. Audit follows durable submission.
Frozen/blocked account/card states must be reported honestly; information tools remain separate
from permission to create requests. Address fields include mode on_file/new, line1/line2/city/state/pin.
Cheque delivery defaults are 7/30 days; debit delivery is 10 days, driven by YAML.

## Ingestion and files

Pipeline.ingest is shared by scripts/ingest_bank_docs.py and customer UploadService.process.
It validates actual file signatures/extensions, size and archive safety; computes a file hash;
deduplicates by file_hash/owner_key; and resumes documents.job checkpoints under data/ingest.
Stages parse, image resolution, chunking, embedding and storage preserve deterministic IDs.
Completed unchanged files are skipped; deferred documents can fill missing vectors later.
Quota pauses preserve job progress. Retryable image failures retain needs_retry and a cooldown.

| Input | Implemented parser path |
|---|---|
| PDF | PyMuPDF text/images/rendering and pdfplumber tables; text-layer decision per page |
| PPTX | python-pptx text, native tables, notes and embedded pictures; no LibreOffice |
| DOCX | python-docx paragraphs/headings/tables/inline images |
| XLSX/CSV | openpyxl/pandas, configured header detection, row records; no vision |
| MD/TXT | Local text/heading parsing |
| PNG/JPEG/WebP | Image cascade |

Element in app/ingest/models.py is the shared parser output: text/table/image, content, page,
section/bbox, source method/confidence, flags and extraction linkage. Parser errors include corrupt,
unsupported, executable and password-protected input; customer caps can yield explicit Partial output.
Current upload limits: 10 MB, five files/session/message, 30 digital or 15 scanned pages/file,
120 chunks/file, 200/session, 5 vision pages/file, 10/session, 5,000 user chunks globally.
Upload expiry is 24 hours; bank ingestion uses the same validation with customer caps disabled.

### Image cascade and extraction evidence

ImageCascade hashes normalized pixels and checks owner-scoped extractions. Cache hits preserve
crop provenance. Tiny/decorative images can be skipped; RapidOCR records blocks/confidence locally.
Classification distinguishes paragraph/table/form/chart/handwriting-like content using configured
keywords/confidence. Clear paragraphs use OCR; hard or critical-numeric crops use configured vision.
Inputs are redacted before provider upload. Local vision uses an actual optional image field,
not an encoded image in the text prompt. The original table/screenshot prompts remain unchanged.
Vision receives mode=ingest for bank documents and mode=online for customer uploads.

app.vision.parse_vision_json tolerates fences; invalid JSON gets one retry. postprocess_vision
checks numbers against unmasked OCR before final PII masking, normalizes O→0/l→1 only inside
number-shaped tokens, drops unsupported notes, and preserves cut-off rows as truncated/null.
Bottom-edge ink can independently identify a clipped final row. Disagreements/range/tenure
checks produce review flags; no invented numeric choice is accepted as a confirmed value.
Unavailable vision returns low-confidence local OCR and needs_retry; not a successful extraction.
extractions stores OCR blocks/text, raw/structured VLM output, per-field comparison and selected
source/reason; image chunks carry extraction_id. Complete OCR-validated rows can remain usable
while flagged/null/truncated rows are excluded from exact lookup. Inspect both readings explicitly.

### Chunks, embeddings and retrieval

chunk_elements splits by sections, with 300–400-token prose, 12% overlap, 1,000-token parents;
tables up to 500 tokens stay whole, larger ones repeat headers by row, with <=80-token row chunks.
All text gets a document/section/page prefix. Structured images render readable tables or
label/value text. rate_records produces dimensions and values for eligible rate tables/rows,
capped at 5,000 rows/table. Content hashes and provenance accompany each chunk.
Embeddings.embed uses 768-dimensional L2-normalized Gemini vectors, separate query/document task
types/cache keys, existing chunk-vector reuse by hash/model and disk cache under data/test_cache.
Document pacing defaults to 80 chunks/minute; retry/backoff is configured. Never mix vector models.

search_knowledge filters namespace/owner/lifecycle and optional product/type/doc; user data also
requires unexpired ownership. It runs vectorSearch and regular $text/textScore separately,
20 candidates each, Python RRF k=60, optional rerank (off). Missing embedding quota falls back
to keyword-only. Parents replace child hits; global defaults are five sections/1,200 tokens.
Best vector score or lexical overlap below 0.55 returns no_good_match with no items.
Local writer additionally trims to three sections/800 tokens and 300 history tokens.
Floats in search envelopes are rounded to three decimals before going to the model.
lookup_rate selects active effective rows and requests missing dimensions rather than guessing.
It remains implemented even though the current local enabled tool list excludes it.

The sample generator creates fictional savings/digital FAQs, fees spreadsheet, current and
superseded rate cards, image-rate PDF, loan eligibility PDF, cheque/card policy DOCX, offer PPTX
with a rate picture, and grievance/fraud text under data/bank_docs. It preserves existing files.

## Session lifetime, UI and API

API routes: POST/GET /api/session; POST /api/chat; POST /api/upload;
GET/DELETE /api/upload/{attachment_id}; GET /api/files/{attachment_id};
POST /api/ui-event; GET /api/health. A built frontend/dist is served at /.
Session cookies bind ownership; client_id binds the active tab. API errors provide inline-safe
messages; SESSION_EXPIRED uses 410, a second tab/chat conflict 409, inaccessible files 404.
The snapshot restores ui_history/cards/auth/flow/options and session tokens on reload.

last_active_at changes on chat/upload/UI action; GET heartbeat only refreshes last_seen.
The 60-second cleanup pass hard-expires idle sessions after session.idle_minutes (15).
UploadService.expire_session removes the session, pending state, owned knowledge/files/caches
and in-memory locks/pacing. Audit remains. Independent TTLs provide a 24-hour upload/session
safety net; expired filesystem orphans/checkpoints are swept separately. Cleanup logs only changes.

React keeps sent bubbles and cards visible immediately. FIFO messages/options queue while a
reply runs; attachment uploads start independently. New chat aborts the browser fetch, clears
pending work and guards against old replies entering the new session. Thinking indicator remains.
Cards have remove controls before send and stay openable afterwards; ownership governs previews.
Images use a viewer, PDF/text open separately, other formats download. Statuses include Uploading,
Processing, Ready, Partial, Failed and expired. UI wording/timing are configured; heartbeat is 30s.
Markdown tables, citation Sources, login/token chips, wrapping options and light/dark plain CSS
are implemented. Responsive layout targets 360px/1280px; this cleanup performed no browser check.
Every UI action is sent to /api/ui-event. Idle/session recovery clears prior chat state and notices.

## Logs, tokens, quota and failures

AppLog.step, span and traced retain request_context and span_stack. One chat/upload trace uses
the request ID; span lines/rotating JSON include module/function, parent/span IDs, timing, status
and attributes. Decisions log why; failures log type/reason. A TRACE TREE and summary follow.
DB operations are DEBUG and summed; idle polling is silent at INFO. Upload completion records
parser/OCR/VLM/embed counts and stage times. Configured rotation is 5 MB with three backups.
Areas: UI, API, SESSION, AUTH, PREPASS, AGENT, TOOL, FLOW, LLM, EMBED, VLM, OCR, PARSE,
INGEST, RAG, VERIFY, DB, UPLOAD and CLEANUP. LOG_LEVEL overrides the configured level.
Masking preserves decimal scores and six-digit references; card matching requires Luhn validity.

TokenMeter tracks input/output/total/calls per session and process for LLM/VLM, excluding
embeddings. Fake/cached calls consume zero. Missing local counts use marked chars/4 estimates;
API responses include request/session totals, persisted at sessions.counters.tokens. Shutdown
prints SESSION TOKENS and PROCESS TOKENS even for sessions already deleted in this process.
The Node launcher forwards Ctrl+C to dev_backend.py, which asks uvicorn to end its lifespan.

QuotaGuard reserves daily requests in quota_usage using the Pacific calendar day; failed
reserved attempts count conservatively. Upload embedding stops at the configured percentage
or query reserve; queries may continue via keyword retrieval. Local native agent calls use
LocalClient directly and do not reserve the remote LLM quota guard; vision still enters _attempt.
Provider budgets are provisional settings, not current account entitlements. Process/session
token totals are not the daily provider ledger. No cleanup command generates a model request.

Failures retain state/progress and return configured safe wording; never present an unreadable
image, unsupported answer or blocked call as verified success. Historical evaluations and build
specifications retained elsewhere are records, not claims about the current local model.
