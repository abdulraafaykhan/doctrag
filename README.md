# Fictional bank assistant demo

Portfolio project with fake data only. The document assistant has username/password login, owned chats, a seeded shared knowledge base and private per-chat attachments. Bank account tools arrive in Phase 4a.

## Local setup (PowerShell)

Set `JWT_SECRET` in your local `.env` to at least 32 random characters. Never commit that file. Other Phase 2 settings are `ACCESS_TOKEN_MINUTES` (default 30) and `CORS_ORIGINS` (a comma-separated list of explicit browser origins). Existing MongoDB, Gemini and Groq settings remain required.

```powershell
Set-Location C:\Projects\doctrag
uv sync
uv run pytest -q
uv run python -m scripts.seed_users
uv run uvicorn app.main:app --reload
```

The seed command needs your live Atlas connection. It inserts missing demo users; re-running preserves existing passwords and account status. Passwords are stored only as Argon2 hashes.

## Fictional demo credentials

These passwords are public fake demo credentials, unsuitable for real systems.

| Username | Fake password | Fixed customer id for Phase 4a |
| --- | --- | --- |
| alice | Demo-Alice-123! | demo-customer-001 |
| bob | Demo-Bob-123! | demo-customer-002 |
| carol | Demo-Carol-123! | demo-customer-003 |

## Swagger verification

Open http://127.0.0.1:8000/docs. Click **Authorize** and log in with a demo username and password. Create a chat with `POST /sessions`, copy its `session_id` into `POST /chat`, and use `stream: false` for a JSON reply. List, resume and delete chats through `/sessions`.

Authorize as a second user: the first user's history, chat and delete requests must return 404. Requests without a token must return 401, except login, health and the documentation routes. `/` redirects to Swagger.

The old `/documents` API is removed. Upload files with `POST /sessions/{session_id}/attachments` and poll `GET /sessions/{session_id}/attachments` until ready. Use the returned `doc_id` values as `attachment_ids` on `POST /chat`. They must belong to this user and chat; foreign ids return 404 before a stream starts. Omit `attachment_ids` on follow-ups: earlier files in the same chat remain searchable. Files are sent to Gemini for embedding and their text is sent to Groq for answers. Use fake data only.

## Demo limitations

Login throttling is process-local: five failures for a username within 15 minutes block further attempts for 15 minutes. It resets on restart and is not shared between workers. Verification is serialized to prevent concurrent throttle bypasses; this limits login throughput. Tokens expire after 30 minutes by default and cannot be revoked before expiry, though locking a user prevents access on subsequent requests. Old unowned chats are not claimed or exposed to new users. There is no public registration or money movement.

Tests use mocked services; live Groq, Gemini and Atlas behavior requires manual verification. Startup marks documents that have remained in `processing` for over 15 minutes as failed. Session/document retention is scheduled for a later phase.


## Phase 3 settings and limits

Optional settings: `RETENTION_DAYS=30`, `ATTACHMENT_WAIT_SECONDS=60`, `USER_UPLOAD_MAX_MB=5`, `USER_MAX_CHUNKS_PER_DOC=150`, `USER_MAX_TABLE_ROWS=500`. Upload up to 3 files per request and 5 per chat. Oversized, empty and unsupported files are rejected. Chunk and table truncation produces warnings on the attachment listing. Table statistics retain the existing behavior of covering all rows read, even when only some row passages are indexed.

User-file duplicate detection uses `(scope, owner_id, session_id, file_hash)`; identical bytes in a different user or chat become a separate attachment. Concurrent uploads reserve chat slots atomically. Duplicate uploads reuse the existing slot; failed documents can be retried. Deleting a chat removes its files and chunks, including chunks from an ingestion still finishing. Attachment expiry follows the session's last message activity and is refreshed with each message. Automated retention purging remains Phase 5 work; no document TTL index independently deletes files ahead of their session.

Old unscoped documents/chunks are deliberately excluded from retrieval and are not automatically promoted into the shared library. Re-upload any needed old file into its intended chat. The shared library is loaded only through the local seed script. No new LLM calls or dependencies were added in Phase 3.

## Update the Atlas vector index before checking retrieval

1. In your Atlas project, open the cluster's **Search / Vector Search** indexes. Locate `chunks_vector_index` on your app database's `chunks` collection (default database: `rag_db`). Use your configured index/database names if overridden.
2. Choose **Edit Index**, use the JSON editor, and replace the complete definition with [atlas/chunks_vector_index.json](atlas/chunks_vector_index.json). Keep the existing name and `vectorSearch` index type. The definition uses `embedding`, 768 dimensions, cosine similarity, and filter fields `doc_id`, `scope`, `owner_id`, `session_id`. Dimensions must match the configured embedding dimension.
3. Save the definition. Editing triggers Atlas's background rebuild. Wait for the new definition to finish building and report **READY**; confirm all four filter fields are in the active definition. Do not use the old index's ability to serve queries as proof the new build is ready.
4. If the index does not exist, create a **Vector Search** index using the JSON editor, the same name/collection and the same JSON file; wait for **READY**. Startup creates a missing index, but intentionally does not overwrite an existing Atlas index. No collection deletion or re-embedding is needed just to add these filter fields.

MongoDB documents `$or` as supported in the `$vectorSearch.filter` logical operators. Retrieval uses one embedding and one pre-filtered vector search for shared documents OR this owner's current chat, further limited to ready scoped document ids. There is no unfiltered fallback. See [MongoDB filtering documentation](https://www.mongodb.com/docs/vector-search/query/aggregation-stages/vector-search-stage/?deployment-type=atlas&embedding=auto&interface=None&language=None) and [index editing documentation](https://www.mongodb.com/docs/vector-search/indexes/vector-search-type/).

## Seed the fictional knowledge base and verify isolation

Only run the following seed with your live Atlas/Gemini credentials configured. It reads `seed_docs/`: fee schedule (CSV table), savings terms, debit card FAQ, loan/EMI FAQ and complaint/dispute process. Re-running replaces shared documents with the same names, leaving user uploads untouched. This uses embedding quota again. A failed ingestion stops the seed; inspect document status and retry.

```powershell
Set-Location C:\Projects\doctrag
uv run pytest -q
uv run python -m scripts.seed_kb
uv run uvicorn app.main:app --reload
```

After the index is READY, open Swagger and authorize. Create a chat and ask "What is the debit card replacement fee?"; expect INR 200 and a fee-schedule citation. Upload a fake text file into that chat, wait for ready, ask about it with `attachment_ids`, then ask a follow-up without them. A second chat or second user must not retrieve that file; explicitly supplying its id must return 404. Delete the original chat and verify its `documents` and `chunks` rows are gone. Atlas indexing of newly inserted chunks is asynchronous; if a just-ready attachment is not found immediately, wait briefly and retry. Rebuilding the index does not by itself seed bank documents.
