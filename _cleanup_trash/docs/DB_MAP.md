# Database map

The configured `harbourline` database has exactly these 15 application collections. User content expires after 24 hours as a TTL safety net; the 60-second worker cleanup hard-deletes all session-owned content after `session.idle_minutes` without user activity. Audit events remain. Heartbeats update `last_seen` only.

| Collection | Stored content | Key fields | TTL | Writer |
|---|---|---|---|---|
| users | Seeded fictional customers | user_id, mobile, address | None | seed_db |
| accounts | Balance, ownership, status | account_id, account_number, balance_paise | None | seed_db |
| cards | Card status and last four digits | card_id, account_id, last4 | None | seed_db |
| transactions | Account ledger | txn_id, account_id, posted_at | None | seed_db |
| requests | Submitted service requests | request_id, ref_no, idempotency_key | None | Flows / tools |
| sessions | Auth, held messages, history, ui_history, uploads, attachments, counters.tokens | session_id, last_active_at, last_seen, expires_at | 24 h safety net; 15 min idle hard deletion | Sessions / API / TokenMeter |
| pending_actions | Confirmation state | token, session_id, idempotency_key | 15 min | Flows |
| audit_log | Masked events without conversation text | event_id, session_id, event_type, expires_at | 30 days | Sessions / tools |
| documents | Source metadata and resumable job | doc_id, file_hash, owner_key, job{state,stage,cursor,chunks_total,chunks_done,attempts,last_error,updated_at} | User 24 h; bank none | Pipeline / UploadService |
| sections | Parent retrieval text | section_id, doc_id, owner_key, section_path | User 24 h; bank none | Pipeline |
| chunks | Readable text, source provenance and reusable normalized vector | chunk_id, doc_id, filename, doc_title, extraction_id, source_method, content_hash, embedding_model, embedding_dims, embedding, review_flags | User 24 h; bank none | Pipeline / Embeddings |
| rate_tables | Table structure and source metadata | table_id, doc_id, columns, source | User 24 h; bank none | Pipeline |
| rate_rows | Exact rates and lookup dimensions | row_id, table_id, doc_id, dims, values, needs_review | User 24 h; bank none | Pipeline |
| extractions | Full OCR, VLM response, field comparison and selection reasons per crop | extraction_id, doc_id, owner_key, image_hash, ocr, vlm, compare, selected_source, selected_reason, flags | User 24 h; bank none | ImageCascade |
| quota_usage | Pacific-day provider request reservations | day, llm_requests, vlm_requests, embed_requests | None | QuotaGuard |

`scripts/inspect_db.py summary` lists row counts and purposes. `session [id]`, `docs`, `chunks <name-or-id>` and `extraction <name-or-id>` are read-only views. `drop-unknown` only lists extra collections; deletion requires `--yes`. `scripts/migrate_storage.py` previews legacy counts; `--apply` renames image_cache in place, copies jobs into documents and reusable vectors into chunks before removing ingest_jobs and embed_cache. Existing Atlas search indexes are unchanged.
