# Redesign: anonymous chat, in-chat verification, guided services (Harbourline Bank)

This file is the **current design**. It overrides `docs/DESIGN.md`. Behaviour of the guided flows is in `docs/SERVICE_FLOWS.md`, the documents and data in `docs/DOCUMENTS_SPEC.md`, the terminal logs in `docs/LOGGING.md`, the order of work in `docs/PHASES.md`.

## 1. Status

**Already built (R1, keep it working):** anonymous server-side sessions with `X-Session-Id`, lazy expiry and wipe, in-chat verification (account, mobile, demo OTP), lockout per hashed account across sessions, per-session and per-IP rate limits, masking, verified-only attachments, customer-change wipe, one-tab lock in the browser, ten fake customers, a plain-code personal-intent helper (`app/verification/intent.py`).

**Known bugs to fix (stage 1 and 2):**
1. The conversation is not shown properly. Every user and bot message must appear (section 6).
2. After a correct OTP the original question is not answered. The replay goes back through the router model and fails.
3. The router (small model) is unreliable at choosing tools.
4. A failed turn hides the cause in the terminal (a provider 404 logged only an exception type).
5. A user message is lost from history when the turn fails.
6. Old shared documents still say "Lantern Demo Bank".
7. Service requests (cheque book and others) have nothing behind them: the bot says it has no information.
8. A question with several parts ("how many loan types and what are the fees?") is not answered fully.

**Dropped:** OCR, CrewAI.

## 2. What the bot does

| Kind | Needs verification | Source |
| --- | --- | --- |
| General bank question | no | documents, with `[n]` citations |
| EMI estimate | no | code (formula), guided flow |
| Balance, recent transactions, card status | yes | bank database, shown as account cards |
| Cheque book, block card, new card, mini-statement, request status | yes | guided flow, then a stored request |
| Greeting, thanks, out of scope, "not found" | no | fixed replies, no LLM |

## 3. Turn pipeline (one function, in this order)

1. Load the session once. Check expiry lazily (section 5). Update `last_activity_at`.
2. Apply the per-session and per-IP rate limits.
3. If the request carries an `option_id`, accept it only if it is one of the options the bot offered at the current step.
4. **Store the user's message first** (masked where needed, section 7), so history is right even if the turn fails.
5. **Active flow?** The flow engine handles the message (`docs/SERVICE_FLOWS.md`). No LLM.
6. **Else the rules layer:** `intent.py` matches clear requests (personal data, a service, EMI, request status) with plain patterns. A match goes straight to the right place. No router LLM call.
7. **Else the router LLM** (small model, one call) chooses only among: `search_documents`, `start_service`, an account tool, or `reply_without_data`. If it returns nothing valid, retry once, then fall back to `search_documents`. The rules layer in step 6 runs again on that fallback as a safety net.
8. **Execute the chosen tool.** Account tools and services check verification in the server, not in the model. If unverified, store the **intended action** (for example `get_balances`, or `start_service: cheque_book`) and start verification.
9. **Build the answer.** A single account tool with no document part is answered by a **code template** (no LLM). Anything with documents is one answer-model call, grounded on the retrieved passages and tool results.
10. Store the bot message (text, options, cards, sources), stream it, write the `turn_done` log line.

**LLM budget per turn:** flows, fixed replies and clear intents use 0 calls. A document question uses 1 router call (when the rules layer did not decide) plus 1 answer call. Never more than 2.

**After verification succeeds,** the bot runs the **stored intended action directly**. It does not re-route the old question through the router. A stored service starts its flow at the first step.

## 4. Routing and tools

Tools shown to the router carry no identity fields.

| Tool | Args | Verification |
| --- | --- | --- |
| `search_documents` | `queries`: 1 to 3 short standalone search queries | no |
| `get_balances` | none | yes |
| `get_recent_transactions` | `account_type?`, `limit?` (default 5, max 20) | yes |
| `get_card_status` | none | yes |
| `start_service` | `service`: `cheque_book`, `block_card`, `new_card`, `mini_statement`, `emi_estimate`, `request_status` | all except `emi_estimate` |
| `reply_without_data` | `kind`: `greeting`, `thanks`, `out_of_scope` | no |

- `queries` is a list so one call can cover a multi-part question (bug 8). Retrieval runs each query, merges and de-duplicates the chunks, then one answer call covers all parts. This works with gpt-oss, which cannot make parallel tool calls.
- The registry marks each tool `requires_verification`. The executor rechecks it even if the router did not.
- Router prompt: short, with a few examples per tool, and an explicit rule that personal-data and service requests must be routed to their tools even before verification. The router sees at most the last 4 short messages, with sensitive values already masked.
- Keep the existing validation, one retry and fallback. Keep `select_tools` handling for gpt-oss if it exists.

## 5. Sessions and timers (built in R1; keep)

- `POST /sessions` returns a random id (`secrets.token_urlsafe(32)`). The browser keeps it in `localStorage` and sends `X-Session-Id`. Unknown or expired id: 404, and the frontend starts a new session.
- **Activity** is a message or an option click. A history fetch, an open tab or a heartbeat is not activity.
- Verification ends after **10 minutes idle** or **30 minutes in total**. The chat stays open. The next personal request asks to verify again, and an interrupted flow **continues at the same step** after re-verification.
- The whole chat is wiped after **30 minutes idle**: session, messages, attachments, chunks. Audit events are kept (no message content). Check expiry lazily on every request; the hourly purge and TTL indexes are a backup.
- **One chat per browser.** "New chat" deletes the old session and wipes everything. A second tab is blocked with "This chat is already open in another tab" (heartbeat in `localStorage` every 10 s, stale after 30 s, no server call).
- A different customer verifying in the same chat wipes the old messages, attachments, chunks and flow (keep the session id) and writes a `customer_changed` audit event. The same customer re-verifying wipes nothing.
- Attachments need a verified session (403 `verification_required` otherwise). They are hidden while verification is expired.

## 6. Visible conversation (frontend and history)

- Render **every** message as a bubble in order, with a clear label: the customer's messages on one side (label "You"), the bot's on the other ("Harbourline").
- The customer's bubble appears **immediately on send**, before the answer streams. If the turn fails, the bubble stays and an error bubble follows (no lost messages).
- Verification inputs are visible too. **Display policy A:** the account number shows masked (`Account ending 1001`), the mobile masked (`Mobile ending 0101`), the OTP as dots (`••••••`). This matches what the server stores, so a page refresh shows the same thing. (Policy B, raw values until refresh, is not used. Changing it later is a one-line change in the display mapper.)
- Bot messages can carry **option buttons**, an **account card**, a **request card**, and **citations**. Buttons are disabled after one is chosen, and the chosen label shows as the customer's next bubble.
- `GET /sessions/{id}/history` returns messages with `role`, `text`, `options`, `cards`, `sources`, `created_at`, so a refresh rebuilds the same screen.
- Markdown for bot text, never raw HTML. Keep citation chips that open the source passage.
- Send box: Enter sends, Shift+Enter is a new line, disabled while a turn is streaming. Scroll to the newest message.

## 7. Sensitive data policy

- Account numbers (12 digits), mobile numbers (10 digits, optional `+91` or `91`) and OTPs are parsed by plain code from the raw text. **Then** the text is masked before it is stored, sent to any model, or logged.
- Mask 12-digit and mobile patterns in chat messages, history and attachment text (before chunking and embedding). Mask a 6-digit value **only** while the verification flow is at the OTP step. Elsewhere six digits are pincodes, cheque numbers, references or amounts.
- Shared knowledge-base documents are not masked. Tool results and account cards keep amounts unchanged and show account and mobile numbers as the last 4 digits only.
- One masking helper, used everywhere. Tests cover each case.
- Raw values exist only in transient server flow state and are cleared when the flow ends, cancels, resets or the session is wiped. OTP values are never stored.

## 8. Verification rules (built in R1; keep)

- Steps: account number (12 digits), then registered mobile (10 digits), looked up **together**. No early "not found". Then the demo OTP: any 6 digits, valid for 5 minutes, "resend" allowed. `OTP_MODE=demo|real`; only `demo` is implemented.
- A non-6-digit OTP reply is re-asked and does not count as a try.
- A wrong account + mobile pair is one try. After **3 tries**, one generic message and the flow resets.
- Failures count per `sha256` of the typed account number across sessions (also for numbers that do not exist). After 3, that number is locked for **15 minutes**, so "New chat" does not reset it. The count resets on success or when the lock ends.
- Limits: `CHAT_RATE_PER_MINUTE=6` per session, `CHAT_RATE_PER_IP_PER_MINUTE=20`, `VERIFY_RATE_PER_IP_PER_MINUTE=10`. IP is `request.client.host` (run uvicorn with `--no-proxy-headers`).
- "Cancel", "stop" or "never mind" ends a flow at any step. A message that does not fit the expected input is answered as a normal question, then the bot offers to continue.
- All fixed wording lives in one module. It is never LLM-generated.

## 9. Settings (placeholders in `.env.example`)

Existing: `SESSION_IDLE_WIPE_MINUTES=30`, `VERIFY_IDLE_MINUTES=10`, `VERIFY_MAX_MINUTES=30`, `VERIFY_MAX_TRIES=3`, `VERIFY_LOCK_MINUTES=15`, `CHAT_RATE_PER_MINUTE=6`, `CHAT_RATE_PER_IP_PER_MINUTE=20`, `VERIFY_RATE_PER_IP_PER_MINUTE=10`, `OTP_MODE=demo`, `OTP_VALID_MINUTES=5`, `LOG_FULL_PROMPTS=false`.
Add: `LOG_LEVEL=INFO`. Remove `LOG_SHOW_SENSITIVE` (credentials are never shown, so the switch does nothing).
Set the model defaults in `config.py` to `openai/gpt-oss-120b` and `openai/gpt-oss-20b`.

## 10. Service requests and bank data

- Ten fixed customers `CUST-001` to `CUST-010` (scenarios in `seed_data/bank/customers.json`). Mobile `9000000101` to `9000000110`. Demo: account `880000001001`, mobile `9000000101`.
- New collection use: `service_requests` with unique `reference` (`HB-CHQ-2026-000123`; prefixes `CHQ`, `BLK`, `CRD`, `STM`) and a unique `flow_id` for idempotency. A `counters` collection gives the sequence number atomically (`find_one_and_update` with `$inc`).
- Fees, turnaround times, loan rates and limits live in **one file**, `seed_data/bank/service_catalog.json`, read by the flows and quoted by the documents. They must always match.
- `scripts/seed_bank.py --reset` restores the fixtures (deletes requests created at runtime, restores card statuses) so the demo can be repeated.
- Money stays integer minor units. The balance of each account equals the sum of its transactions. Service fees are quoted, not posted, so this stays true.

## 11. Audit events (no content)

`session_created`, `verify_started`, `otp_sent`, `verify_ok`, `verify_failed`, `verify_locked`, `verify_expired`, `customer_changed`, `session_wiped`, `session_expired`, `flow_started`, `flow_completed`, `flow_cancelled`, `service_request_created`, `card_blocked`, `tool_call`, `rate_limited`.