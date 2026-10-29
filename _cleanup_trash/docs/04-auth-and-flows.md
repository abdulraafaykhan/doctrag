# 04 Authentication and guided flows

## Principle
Code owns state. The agent only fills slots and answers questions. Fast paths (OTP digits, "confirm", "cancel", exact button option) skip the LLM entirely.

## Auth states
`ANONYMOUS` -> `AWAITING_OTP` -> `VERIFIED`. Stored in `sessions.auth`.

| From | Event | To | Code does |
|---|---|---|---|
| ANONYMOUS | personal request, details missing | ANONYMOUS | hold message, ask only the missing detail(s) |
| ANONYMOUS | mobile + account known, pair matches a seeded user | AWAITING_OTP | hold message, "send" OTP (demo: show a notice that any 6 digits work) |
| ANONYMOUS | pair does not match | ANONYMOUS | generic error ("I couldn't verify those details"), count failure, lock after 3 |
| AWAITING_OTP | exactly 6 digits | VERIFIED | in demo mode any 6 digits pass; set verified_at; replay held messages |
| AWAITING_OTP | wrong format / expired | AWAITING_OTP | count attempt (max 3), offer resend (max 3) |
| AWAITING_OTP | "cancel" | ANONYMOUS | clear held messages and candidates |
| Any session | session.idle_minutes without user action | SESSION_EXPIRED | delete session, auth, history, pending actions and all owned upload data; heartbeat does not extend activity |
| VERIFIED | a different account number is given | AWAITING_OTP | full re-login, old data not shown |

Rules:
- One message may contain mobile, account and the question. Extract all three, hold the question, ask only for what is missing.
- Never reveal which of mobile/account was wrong, or whether an account exists.
- A 6-digit number is an OTP only when state is AWAITING_OTP. Formats are fixed (10-digit mobile, 12-digit account) so regex can tell them apart.
- Lock: after 3 failed pair checks or 3 wrong OTPs, lock for `auth.lock_minutes` (config).
- A public question asked while AWAITING_OTP is answered; the OTP state stays alive.
- OTP stored as a hash with expiry (5 min). `verify_otp()` is the only place that compares; demo mode is a config flag.
- **Replay:** after verification, run each held message (masked) through the agent again with the verified tool set, in order, and send ONE combined reply. The customer must not repeat the question or re-upload an attachment.
- Held attachments: images are read before login (no personal account data is exposed by reading the customer's own screenshot); store the result with the held message and answer after OTP without asking again.

## Flow engine
Each service is a YAML definition in `config/flows/<name>.yaml`: slots in order, allowed values, synonyms, button labels, validators, the summary template, and the executor name. Adding a service = a new YAML + a typed `<name>_set_slots` tool.

State in `sessions.active_flow`: `{flow, step, slots, status, pending_token}`. One flow at a time.
- `COLLECTING` -> agent calls `*_set_slots` (may fill several slots at once). Code validates each slot.
- Summary: agent calls `flow_summary`. Code locks slots, creates `pending_actions` (15 min), sets `AWAITING_CONFIRM`, returns `display` = summary of ALL answers from the start.
- At `AWAITING_CONFIRM` code reads the customer's reply directly (no LLM): "confirm / yes / ok submit" -> submit; "change <slot>" -> back to COLLECTING at that slot (then a new summary); "cancel" -> end flow. Anything else is treated as a side question: answer it, the summary stays valid, and the confirm question is repeated.
- Ambiguous words ("ok maybe", "hmm") are NOT a confirm. Ask: confirm, change something, or cancel.
- Submit (code only): create `requests` atomically with `idempotency_key = hash(user_id + flow + final slots + token)`; double confirm creates one request. Reply with reference number and expected timeline. Write `audit_log`.
- A pending action older than 15 minutes is expired: say so and offer to restart.
- A second flow request mid-flow: ask to finish or cancel the current one first.
- Side question mid-flow: agent answers via knowledge tools then repeats the next flow question (the flow block in the system prompt tells it the next question).
- Corrections ("make it 60") call `*_set_slots` again; the summary updates.
- User abandons or changes topic: agent confirms cancelling via `flow_cancel`; flows also expire after `flow.idle_minutes`.

## Cheque book flow (`config/flows/chequebook.yaml`)
1. pages: options **20, 40, 60 only** (buttons). Typed numbers/words map only to those three ("40", "forty", "the 40 one"). "50" -> re-ask with the three options.
2. delivery: **emergency** (within 1 week) or **normal** (20-30 days). Synonyms: urgent, fast, express, regular, standard. Button labels show the timelines.
3. address: "address on file" (show a short masked version) or new address {line1 5-120 chars, city, state, pin exactly 6 digits}.
4. summary: pages, delivery type, address. Buttons: Confirm / Change / Cancel.
5. submit -> reference like `CB-<yymm>-<4 digits>`. Expected date from delivery type.
Pre-fill: "40 page chequebook urgent" fills pages and delivery; the flow starts at the address step.

## Debit card flow (`config/flows/debit_card.yaml`)
1. card_type: standard or premium. 2. address (same as above). 3. summary. 4. submit -> reference `DC-<yymm>-<4 digits>`. No emergency option. Reject if the customer already has an active card of the same type pending (check `requests`).

## Statement and balance
Not flows. `get_statement` asks for the period if missing (options: last 5, last 10, last 30 days, custom). Present the table from `display`.

## Edge cases the engine must handle
Mixed public and personal messages (answer public now, hold personal until OTP); several personal requests in one message; image attached before login; user types an OTP or PIN in a normal sentence (never store it in history, audit log or any LLM prompt); rapid double submit; session expiry mid-flow; frozen account or blocked card (state it honestly, still allow info tools); blocked user (generic failure).
