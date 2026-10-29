# Database map

MongoDB database: `database.name` in config/settings.yaml, currently harbourline.
app/db.py owns one shared MongoClient (pool 10, configured timeout 15 seconds).
Money is integer paise; timestamps are UTC, plain dates ISO YYYY-MM-DD.
Seed identities/addresses are fictional: ten customers, 48 transactions/account over 120 days,
with a frozen account and blocked card. Cards store last4, not full numbers or secrets.

## The 15 collections

| Collection | Stored data and key fields | TTL / retention | Writers |
|---|---|---|---|
| users | Synthetic customer identity: user_id, full_name, mobile, account_id, address, status, created_at | None | scripts/seed_db.py |
| accounts | account_id/number, user_id, type, balance_paise, currency, status, branch, opened_on | None | scripts/seed_db.py |
| cards | card_id, user_id/account_id, network, card_type, last4, expiry_month/year, status, daily_limit_paise | None | scripts/seed_db.py |
| transactions | txn_id, account_id, posted_at, credit/debit direction, amount_paise, balance_after_paise, description, channel, reference | None | scripts/seed_db.py |
| requests | request_id, idempotency_key, ref_no, source_token, type, user_id/account_id, details/slots, status, submitted_at/created_at, expected_by; also handoffs | None | Flows.submit, Registry.handoff |
| sessions | session_id, client_id, auth, active_flow, history, ui_history, uploads, attachments, counters.tokens, timestamps, lease fields | expires_at TTL; 24h safety net, 15min user-idle hard cleanup | Sessions, Chat, API, UploadService, TokenMeter |
| pending_actions | token, session_id, user_id/account_id, flow, slots, status, expires_at; idempotency_key after confirmation | expires_at TTL; 15min pending lifetime | Flows.summary/submit/cancel |
| audit_log | event_id, session_id, event_type or tool, masked args/code, user_id/ref_no when relevant, created_at, expires_at | expires_at TTL; 30 days | Sessions.audit, BoundTool.invoke, Flows.submit |
| documents | doc_id, namespace, owner_key, file_hash, filename, title/type/product/version/effective_date/lifecycle, mime, size_bytes, storage_path, processing/embedding state, counts/review flags, job, attachment, timestamps | expires_at TTL on user uploads (24h); bank rows no expiry | Pipeline, UploadService |
| sections | section_id, doc_id, namespace/owner/lifecycle, section_path, page bounds, parent text/token_count, expiry | User expiry; bank none | chunk_elements via Pipeline |
| chunks | chunk_id, doc_id, section_id, namespace/owner/lifecycle, provenance, readable text, hash and vector fields, review state | User expiry; bank none | Pipeline, Embeddings, chunk_elements |
| rate_tables | table_id, doc_id, namespace/owner, product/name, columns, row_count, effective_date/lifecycle, source/page, confidence | User expiry; bank none | Pipeline / rate_records |
| rate_rows | row_id, table_id/doc_id, namespace/owner, product/lifecycle/effective_date, dims, values, row_text, source/confidence/review | User expiry; bank none | Pipeline / rate_records |
| extractions | One crop/image's OCR, VLM, field comparison, selection reasons and flags; extraction_id, doc_id, owner_key/namespace, filename/page/bbox/hash/kind | User expiry; bank none | ImageCascade |
| quota_usage | Pacific date day, embed_requests, vlm_requests, llm_requests, updated_at | None | QuotaGuard.reserve |

## Ownership and source provenance

Knowledge collections use namespace bank/user and owner_key bank/session_id.
Retrieval requires lifecycle active; older versions remain superseded rather than deleted.
Bank metadata is not customer-upload data. User retrieval also requires unexpired rows.
Original images/files live on disk, not MongoDB. Budget approximately 10KB per embedded chunk;
the earlier storage plan targeted 20,000 total chunks, not an enforced database-wide cap.

documents.job replaces the former standalone job collection:
`{state, stage, cursor, chunks_total, chunks_done, attempts, last_error, updated_at}`.
States can retain queued/running/paused quota/needs_retry progress; checkpoints live in data/ingest.
Unchanged completed hashes are skipped; deferred vectors can be filled without reparsing.

Every chunk carries filename, doc_title, page, section_path, kind,
source_method (text_layer/ocr/vlm), extraction_id for images, confidence, needs_review,
review_flags, content_hash, embedding_model, embedding_dims, embedding when available,
created_at and expires_at. Text is readable prose/markdown or label/value lines with context.
Vectors are 768-dimensional and normalized. Existing chunks reuse vectors by content hash/model;
query vectors use a separate disk-cache key. There is no standalone embedding-cache collection.
Parent sections are bounded separately from child chunks; rate row dimensions include tenure,
amount bands and segment, with values such as rate_percent/fee_percent/amount_paise.
Exact lookup excludes unsafe/incomplete rows and picks the effective active version.

extractions was migrated from the former image-cache collection. Its detailed fields are:
- extraction_id, doc_id, owner_key, namespace, filename, page, bbox, image_hash,
  kind, created_at, expires_at, selected_source, selected_reason, flags, needs_review.
- ocr: ran, avg_confidence, full text and blocks of text/conf/bbox; mobile/account/card masked.
- vlm: ran, why_ran, model, text, structured, confidence.
- compare: status agree/disagree/ocr_only/vlm_only/not_compared, agreed_count,
  disagreed_count, fields with path, ocr_value, vlm_value, match, selected, reason.
Table paths look like row 3 / col Rate; slips/forms use field names. Cut-off cells stay null.
Legacy cache rows may retain their old payload with empty provenance until re-extraction.
`scripts/migrate_storage.py` is retained as an ASK item: it previews legacy counts, while
its --apply path can rename/migrate/remove old collections. It was not run during cleanup.

## Sessions and security

auth holds state ANONYMOUS/AWAITING_OTP/VERIFIED; failures, held requests, candidates,
otp_hash, otp_expires_at, lock/resend state and candidate/verified user/account IDs.
The actual stored keys are defined by Sessions, not the former nested-otp design sketch.
Held requests contain text and attachments. active_flow contains flow, slots, status and
pending token/timing/change state. The last confirmed request may be kept as last_submission.
History is masked for mobile/account/card; model history excludes detected OTP codes.
ui_history separately preserves the user's typed demo code. PIN/CVV/password labels stay masked.
uploads, attachments and counters share this session row; no extra history/token collection exists.
Counters contain upload/embedding/vision usage and tokens `{in,out,total,calls}`.
Embeddings are not token-metered; cached/FakeLLM calls use zero tokens.

last_active_at changes for user actions; last_seen is the independent tab heartbeat.
Expired chat/upload/heartbeat requests receive SESSION_EXPIRED; creating a fresh session is explicit.
UploadWorker/UploadService delete idle sessions and owned knowledge/files/caches; audits remain.
The idle deadline is session.idle_minutes. Mongo TTL is a safety net, not immediate filesystem cleanup.
Service request submission is atomic set-on-insert with a unique idempotency key; status is initially
pending in the current code. References/expected dates come from flow YAML. Handoff rows have
their own type/reason fields. Audit tool and auth event shapes differ; readers must allow both.

## Indexes (app.db.normal_indexes/search_models)

- Unique: users.mobile/user_id; accounts.account_number/account_id; cards.card_id;
  transactions.txn_id; sessions.session_id; pending_actions.token; requests.idempotency_key;
  documents (file_hash, owner_key); quota_usage.day; sparse extractions.extraction_id.
- Normal: transactions (account_id, posted_at descending), requests (user_id, submitted_at
  descending), documents (namespace, owner_key, lifecycle), sections/chunks/rate_tables doc_id,
  chunks (content_hash, embedding_model), rate_rows (product,lifecycle)/table_id,
  extractions doc_id/(image_hash,owner_key), audit_log (user_id,ts).
  The audit index's legacy ts key does not mean every current event writes a ts field.
- expires_at TTL, expireAfterSeconds=0: sessions, pending_actions, audit_log, documents,
  sections, chunks, rate_tables, rate_rows, extractions. Bank rows without dates do not expire.
- Regular English text index chunks_text on chunks.text; filtered $text/textScore keyword search.
- One Atlas search index: chunks_vector (configured name), cosine vector on embedding,
  768 dimensions; filter fields namespace, owner_key, lifecycle, doc_type, product.
  The earlier three-slot cluster plan allocates one project index and two spare slots;
  other applications may already occupy them. Cleanup does not inspect/change shared indexes.

scripts/create_indexes.py creates missing indexes and waits for READY/queryable status;
startup checks readiness rather than automatically creating indexes. RRF merges in Python.

## Read-only inspection and optional data cleanup

`scripts/inspect_db.py summary`, `session [id]`, `docs`, `chunks <name-or-id>` and
`extraction <name-or-id>` show counts/purpose, session history/tokens, processing state,
readable chunk/vector provenance, and separate OCR/VLM/field-selection tables respectively.
`drop-unknown` lists unknown collections; --yes would drop those collections. Do not use it
as part of normal setup. `scripts/clean_test_data.py` defaults to count-only, with fixed
collection/predicate allowlists. --yes deletes conversation/request/audit rows and only
namespace user knowledge rows, never fixtures, quotas, bank rows or files.
