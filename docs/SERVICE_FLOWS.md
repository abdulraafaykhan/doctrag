# Guided service flows

How the bot talks to the customer, step by step. Tests assert on **steps, option ids and state**, not on exact sentences, so wording can be polished. All wording lives in one module.

## 1. The engine (keep it small)

A flow is **data**: a name, a flag `requires_verification`, a list of steps, and a `finish` action. A single generic engine runs any flow.

Each step has: an `id`, a prompt builder, options (optional), a parser that validates the answer, and an optional `skip` rule (for example skip "choose account" when there is only one).

Flow state is stored in the session: `flow = {name, step, data, flow_id, started_at}` plus `after_verify` when verification must come first. One flow at a time.

### Rules for every flow

1. **Buttons and typing both work.** A bot message may carry options `[{id, label}]`. A click sends the `option_id`; the server accepts it only if it belongs to the current step. A typed answer is matched against option ids, labels, the 1-based number, and simple aliases ("urgent", "yes"). Numeric options match by value ("50"). The typed answer must match a valid option. No 1-leaf or 49-leaf cheque book is possible.
2. **Invalid answer:** re-ask the same step with its options ("Please choose one of the options below."). The step does not change.
3. **Cancel always works.** The word "cancel", "stop" or "never mind", or the Cancel button on every step. Message: nothing was changed. The flow state is cleared.
4. **Unrelated question mid-flow.** If the message does not parse for the step and looks like a question (ends with `?`, starts with a question word such as what, how, when, why, can, is, do, does, where, which, or has at least 5 words), answer it through the normal pipeline, then add "Shall we continue with your <service> request?" with options `[continue, cancel]`. The flow state is kept. `continue` repeats the current step prompt. Anything else that does not parse is re-asked (rule 2).
5. **Verification guard.** Before every step the engine checks the session is still verified. If it expired, the bot starts verification (account, mobile, OTP). After success the flow **continues at the same step**. If a different customer verifies, the flow is wiped with the chat.
6. **Confirm step.** Every flow that changes anything ends with a summary and `[confirm, cancel]`. Nothing is written before `confirm`.
7. **Idempotent finish.** The `flow_id` is unique on the created request. A double click or a repeated `confirm` returns the same reference and creates nothing new. Clear the flow state in the same atomic step as creating the request.
8. **The account list** shows only the verified customer's accounts as `Savings ending 1001`. Option ids are `acct_1`, `acct_2`, ..., mapped by the server. Real account ids are never sent to or accepted from the client.
9. **Every created request** gets a reference (`HB-CHQ-2026-000123`), status `received`, and an audit event. The final message states the reference and the expected time.
10. **Rate and speed.** Flows use no LLM calls.

## 2. Verification flow (kind `verify`)

| Step | Bot says | Options | Accepts |
| --- | --- | --- | --- |
| `account` | Ask for the 12-digit account number (and why: to access personal details) | `cancel` | 12 digits, spaces or dashes allowed |
| `mobile` | Ask for the registered 10-digit mobile | `cancel` | 10 digits, optional +91 or 91 |
| `otp` | "In demo mode, an OTP was sent to your mobile ending XXXX. Enter any 6 digits." | `resend`, `cancel` | exactly 6 digits |

After success: "Thank you, you're verified." then run the stored action (a data answer, or the first step of a service). Failure and lock messages are the one generic message from `REDESIGN.md` section 8. The customer's bubbles show the masked values (policy A).

## 3. Cheque book (`cheque_book`)

1. `account`: skip if the customer has one account that can have a cheque book. Otherwise ask which account.
2. **Eligibility check (code, before asking anything else).** Not eligible when: the account is not `active`, the account type has no cheque book (`cheque_book_eligible` false), KYC is incomplete, or there is already an open cheque book request (`received`, `processing` or `dispatched`) for that account. Say the specific reason (for a pending request, quote its reference and status), suggest the branch where useful, and end the flow.
3. `leaves`: options `25`, `50`, `75`, each with its fee from the catalog.
4. `speed`: `normal` (5 to 7 working days, free) or `urgent` (2 to 3 working days, extra fee), values from the catalog.
5. `address`: show the **registered address** and ask "Deliver to this address?" with `deliver_here` and `change_address`. `change_address` explains that the address can only be changed at the branch or through the branch helpline, then re-asks the step (`deliver_here`, `cancel`).
6. `confirm`: summary of account (masked), leaves, speed, total fee ("debited when dispatched"), address, expected delivery date. Options `confirm`, `cancel`.
7. `finish`: create the request, answer with the reference, "your cheque book will be delivered to your registered address within X working days", and "you can ask me for the status any time".

Example (CUST-001, verified):
```
You:      I want a chequebook
Bot:      Your Savings account ending 1001 is eligible. How many leaves do you need?   [25] [50] [75] [Cancel]
You:      50                       (typed, or the button)
Bot:      Which delivery do you prefer?   [Normal: 5-7 working days, free] [Urgent: 2-3 working days, Rs 150] [Cancel]
You:      Urgent
Bot:      Deliver to your registered address: <address>?   [Deliver here] [Change address] [Cancel]
You:      Deliver here
Bot:      Please confirm: 50 leaves, urgent, Rs 200 total, to <address>, expected by <date>.   [Confirm] [Cancel]
You:      Confirm
Bot:      Done. Your request HB-CHQ-2026-000124 is received. It will arrive at your registered address within 2-3 working days.
```

## 4. Block card (`block_card`)

1. `card`: skip if there is only one card. Otherwise ask which card (`Debit card ending 4321`, `Credit card ending 9876`).
2. If the chosen card is already blocked, say so, show the replacement offer (step 5) and end.
3. `reason`: options `lost`, `stolen`, `fraud`, `damaged`, `other`.
4. `confirm`: "Block your debit card ending 4321? You will not be able to use it." Options `confirm`, `cancel`.
5. `finish`: set the card status to `blocked` with a guarded update (only if not already blocked), create the request (`HB-BLK-...`, status `completed`), write the `card_blocked` audit event. Answer with the reference. Then offer a replacement: options `new_card`, `no_thanks`. `new_card` starts the new card flow.

The card status tool must show `blocked` afterwards. Blocking is instant and free.

## 5. New or replacement debit card (`new_card`)

1. `account`: choose the account (active savings or current). Skip if only one.
2. Eligibility: account active, KYC complete, no open card request for the account. Otherwise explain and end.
3. `network`: options `rupay`, `visa`.
4. `reason`: options `replacement` (replaces a blocked, lost or damaged card), `additional` (a second card).
5. `address`: as in the cheque book flow.
6. `confirm`: network, reason, fee from the catalog (quoted, debited on dispatch), address, expected delivery. `confirm` or `cancel`.
7. `finish`: create the request (`HB-CRD-...`, status `received`). The new card arrives with a PIN mailer; the old card stays as it is (the bot never reveals card numbers).

## 6. Mini-statement (`mini_statement`)

1. `account`: skip if only one.
2. `finish` immediately (no confirm; it only reads): show the **last 10 transactions** as a table card (date, narration, amount, balance), plus a closing balance line, from code with no LLM. Create a request record (`HB-STM-...`, status `completed`) so it appears in request status.

## 7. EMI estimate (`emi_estimate`, no verification)

1. `loan_type`: options `home`, `personal`, `car`, `education`.
2. `amount`: ask for the amount in rupees. Accept `500000`, `5,00,000`, `5 lakh`, `5.5 lakh`, `2 crore`. Validate against the catalog range for that loan type. On a value outside the range, say the allowed range and re-ask.
3. `tenure`: ask for years or months, for example `5 years` or `60 months`. A bare number is re-asked with the unit. Validate against the catalog range.
4. `finish`: compute in code (reducing balance): `EMI = P * r * (1+r)^n / ((1+r)^n - 1)` with `r = annual_rate / 12 / 100`. Show EMI, rate, total interest, total payable. Round half up to whole rupees. Add the disclaimer: "indicative only, not an offer; processing fees and taxes are extra" and where to apply (branch or the loans document). Options `try_another`, `done`. No request record.

Check value: Rs 5,00,000 at 10% for 60 months gives an EMI of about Rs 10,624.

## 8. Request status (`request_status`)

- Needs verification. If the message contains a reference (`HB-...`), show that request. It must belong to the verified customer. Otherwise answer "I couldn't find a request with that reference", the same message as for a reference that does not exist.
- With no reference, list the customer's latest 5 requests as a card (reference, type, status, created date). If none, say there are none.
- Demo note: statuses are static and do not advance by themselves.

## 9. Data answers (no flow)

- `get_balances`: all accounts as an account card. `get_card_status`: all cards as a card. Both are answered by a **code template** with no LLM call.
- `get_recent_transactions`: if the customer has more than one account and none was named, ask which account (`acct_1`, `acct_2`, ...) using a one-step flow, then show up to the requested number as a table card.
- A mixed question ("what is the overdraft fee and what is my balance?") uses the documents and the account data together in one answer-model call.

## 10. Scripted conversations that must work (acceptance)

Use fixture customers. Each must end exactly as described.

1. **Unverified cheque book (CUST-001):** "I want a chequebook" asks for the account, mobile and OTP, then continues straight into the cheque book flow (no "answer the original question" detour), then the steps in section 3.
2. **Expiry mid-flow:** during the cheque book flow after verification expires, the next answer triggers re-verification and the flow resumes at the same step.
3. **Invalid input:** typing `49` for leaves re-asks with `25`, `50`, `75`.
4. **Unrelated question:** during `leaves`, ask "what are your branch timings?" The bot answers from the documents and offers to continue.
5. **Not eligible:** CUST-006 (account with no cheque book), CUST-007 (incomplete KYC) and CUST-008 (pending request, quote its reference) each get the right explanation.
6. **Block then replace (CUST-005 has a blocked card, CUST-001 has an active card):** block CUST-001's card with reason `lost`, see status `blocked` in the card tool, accept the replacement offer, and finish with a `HB-CRD` reference. CUST-005 gets "already blocked" and the replacement offer.
7. **Mini-statement (CUST-004, two accounts):** asks which account, then 10 transactions.
8. **EMI without verification:** home loan, `35 lakh`, `20 years`, returns the EMI from the catalog rate. Out-of-range amounts are re-asked.
9. **Request status:** after step 1, "status of HB-CHQ-..." shows it, and a reference belonging to another customer returns "couldn't find".
10. **Double click:** two `confirm` messages in a row create one request.