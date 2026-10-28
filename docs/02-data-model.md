# 02 Data model (MongoDB Atlas M0, database `harbourline`)

## Conventions
- Money: integer paise (`amount_paise`). Code formats ₹ (Indian digit grouping, e.g. ₹1,25,000.50).
- Timestamps UTC; show IST. Plain dates `YYYY-MM-DD`.
- IDs: readable strings (`usr_001`, `acc_001`, `doc_<8hex>`) or uuid4 for tokens.
- Formats: mobile 10 digits starting 6-9; account number 12 digits; PIN 6 digits; OTP 6 digits.
- Cards store `last4` only. Never store full card numbers, CVV, PIN or a real OTP.
- `lifecycle` = `active` or `superseded` on documents, sections, chunks, rate tables, rate rows.
- `owner_key`: `"bank"` for bank content, `session_id` for user uploads. User content has `expires_at`; bank content does not.
- Exactly ONE shared `MongoClient` for the whole app (Atlas M0 allows 100 connections).

## Customer data (seeded from `seed/*.json`, 10 fake customers)
- `users`: user_id, full_name, mobile (unique), account_id, address{line1,line2,city,state,pin}, status(active/blocked), created_at
- `accounts`: account_id, account_number (unique), user_id, type(savings/current), balance_paise, currency("INR"), status(active/frozen), branch, opened_on
- `cards`: card_id, user_id, account_id, network(visa/rupay/mastercard), card_type(standard/premium), last4, expiry_month, expiry_year, status(active/blocked/expired), daily_limit_paise, issued_on
- `transactions`: txn_id, account_id, posted_at, direction(credit/debit), amount_paise(positive), balance_after_paise, description, channel(upi/neft/imps/atm/pos/cheque/cash), reference
Seed at least 40 transactions per account over the last 120 days, with consistent running balances. Include one frozen account and one blocked card for edge cases. Mobile numbers and account numbers must be obviously fake.

## Chat and requests
`sessions`: session_id (unique), created_at, last_active_at (user actions only), last_seen (tab heartbeat), expires_at (24 h TTL safety net). `session.idle_minutes` (15) hard-deletes the session and all owned uploads; heartbeat never extends this deadline.
- auth: state (ANONYMOUS | AWAITING_OTP | VERIFIED), mobile_candidate, account_candidate, user_id, verified_at, otp{code_hash, issued_at, expires_at, attempts, resends}, locked_until, failures
- held_messages (max 3): {text_masked, attachment_ids[], received_at}
- active_flow: null or {flow, step, slots{}, status(COLLECTING|AWAITING_CONFIRM), pending_token, started_at, updated_at}
- history (last 12 model turns): mobile/account/card masked; six-digit document numbers are preserved. The separate `ui_history` in this same row preserves typed demo OTPs; verification codes never enter model history.
- uploads (max 5 doc_ids), attachments (records the UI shows: id, name, mime, size, kind, status)
- counters: {embed_chunks_used, vlm_pages_used, uploads_count, tokens{in,out,total,calls}}; cached/FakeLLM calls use zero tokens. API responses expose request and session totals; embedding tokens are excluded.
- client_id: the one active browser tab

`pending_actions`: token (unique), session_id, user_id, action_type(chequebook|debit_card), details{}, summary_text, status(pending|confirmed|cancelled|expired), idempotency_key, created_at, expires_at (15 min, TTL)
`requests`: request_id, ref_no (e.g. CB-2610-0042), user_id, account_id, type(chequebook|debit_card|handoff), details{}, status(submitted|in_progress|dispatched|delivered|cancelled), submitted_at, expected_by, idempotency_key (unique), source_token
`audit_log`: log_id, ts, session_id, user_id?, event_type(tool_call|otp_sent|otp_verified|otp_failed|submit|upload|delete), tool_name, args_masked{}, result_code, latency_ms, model_used, expires_at (30 days TTL)

## Knowledge
`documents`: doc_id, namespace(bank|user), owner_key, filename, file_hash(sha256), mime, size_bytes, storage_path, title, doc_type(faq|policy|rate_card|offer|other), product, version, effective_date, lifecycle, processing_state(queued|processing|ready|partial|failed), pages_total, pages_processed, error_message, counts{chunks,ocr_pages,vlm_pages,tables}, review_flags[{page,reason}], created_at, expires_at(user only)
Resumable work is embedded in `documents.job`: {state,stage,cursor,chunks_total,chunks_done,attempts,last_error,updated_at}; transient vision failures retain `needs_retry` and the parse checkpoint.
`sections` (parents, <= ~1000 tokens): section_id, doc_id, namespace, owner_key, lifecycle, section_path, page_start, page_end, text, token_count, expires_at
`chunks`: chunk_id, doc_id, filename, doc_title, section_id, namespace, owner_key, lifecycle, doc_type, product, version, effective_date, kind(prose|table|table_row|image_text), section_path, page, bbox?, readable text (starts "Doc title > Section > Page N"; tables or label/value lines, never raw structured JSON), token_count, content_hash, embedding(768, normalized; absent until embedded), embedding_model, embedding_dims, source_method(text_layer|ocr|vlm), extraction_id (image chunks), confidence, needs_review, review_flags, created_at, expires_at. Document embeddings reuse an existing chunk with the same content_hash and embedding_model; query embeddings remain separately disk-cached.
`rate_tables`: table_id, doc_id, namespace, owner_key, product, name, columns[{key,label,type,unit}], row_count(<=5000), effective_date, lifecycle, source{page}, source_method, confidence
`rate_rows`: row_id, table_id, product, lifecycle, effective_date, dims{tenure_months, amount_min_rupees, amount_max_rupees, segment}, values{rate_percent, fee_percent, ...}, row_text, source{doc_id,page}, confidence

## Extractions and quota
- `extractions` (renamed in place from image_cache): extraction_id, doc_id, owner_key, namespace, filename, page, bbox, image_hash, kind(table|form|screenshot|paragraph|handwriting), created_at, expires_at; `ocr`{ran,avg_confidence,text,blocks[{text,conf,bbox}]}; `vlm`{ran,why_ran,model,text,structured,confidence}; `compare`{status(agree|disagree|ocr_only|vlm_only|not_compared),agreed_count,disagreed_count,fields[{path,ocr_value,vlm_value,match,selected(ocr|vlm|none),reason}]}; selected_source, selected_reason, flags, needs_review. OCR text is complete except mobile/account/card masking. Paths use `row 3 / col Rate` or the form field name. Old rows retain their original cached result with empty new provenance fields until re-extracted.
- There are no ingest_jobs or embed_cache collections. `scripts/migrate_storage.py --apply` preserves jobs in documents, vectors in chunks and old image rows in extractions. `scripts/inspect_db.py` exposes summary/session/docs/chunks/extraction; drop-unknown is a listing unless --yes is supplied. See DB_MAP.md for the exact 15-collection map.
- `quota_usage`: day (Pacific date, unique), embed_requests, vlm_requests, llm_requests, updated_at

## Indexes
Normal (do not count toward the Atlas search limit):
users.mobile unique; accounts.account_number unique; transactions (account_id, posted_at desc); sessions.session_id unique + TTL expires_at; pending_actions.token unique + TTL; requests.idempotency_key unique + (user_id, submitted_at desc); audit_log (user_id, ts) + TTL; documents (namespace, owner_key, lifecycle) + unique (file_hash, owner_key) + TTL; sections/chunks doc_id + TTL; chunks (content_hash,embedding_model); rate_rows (product,lifecycle) + table_id; extractions.extraction_id sparse unique + doc_id + (image_hash,owner_key) + TTL; quota_usage.day unique.

Atlas search indexes (free cluster allows 3 in total; this project uses 1, leaving 2 spare):
1. Vector index on `chunks.embedding`, 768 dims, cosine; filter fields: namespace, owner_key, lifecycle, doc_type, product.
Keyword search uses the regular `chunks_text` text index on `chunks.text` (English), with the same filters applied in the query; it does not consume an Atlas search-index slot.
Create indexes in `scripts/create_indexes.py` and wait for the Atlas vector index to be READY. Hybrid merge is done in Python (reciprocal rank fusion, constant 60); do not depend on `$rankFusion`.

## Storage budget
~10 KB per chunk including vector. Plan for at most 20,000 chunks (bank + user). Do not store images or original files in Mongo.
