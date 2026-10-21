# Fictional bank assistant demo

Portfolio project with fake data only. Phase 2 adds username/password login and owned chats to the document assistant. Bank account tools and private per-chat attachments arrive in later phases.

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

The existing `/documents` API now requires login but remains a global document library until Phase 3. Do not upload private data. Files are sent to Gemini for embedding and their text is sent to Groq for answers.

## Demo limitations

Login throttling is process-local: five failures for a username within 15 minutes block further attempts for 15 minutes. It resets on restart and is not shared between workers. Verification is serialized to prevent concurrent throttle bypasses; this limits login throughput. Tokens expire after 30 minutes by default and cannot be revoked before expiry, though locking a user prevents access on subsequent requests. Old unowned chats are not claimed or exposed to new users. There is no public registration or money movement.

Tests use mocked services; live Groq, Gemini and Atlas behavior requires manual verification. Startup marks documents that have remained in `processing` for over 15 minutes as failed. Session/document retention is scheduled for a later phase.
