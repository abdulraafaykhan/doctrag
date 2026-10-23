# Terminal logging

Goal: someone watching the uvicorn terminal can follow every turn: what came in, how it was routed, what each model call did, which tool ran, how long it took, and why anything failed.

## 1. Rules

- Standard `logging`, configured once in `app/logging_setup.py`. Output only to the uvicorn terminal. No new dependency.
- One line per event, in a fixed layout, readable at a glance. Colour the level with ANSI codes only when the output is a terminal.
- Every line carries the **turn id** and the **short session id** (first 6 characters).
- Log only actions that reach the server.
- Summaries by default. `LOG_FULL_PROMPTS=true` additionally logs, at DEBUG level, the full prompt and the full reply of each LLM call. Prompts are already masked because masking happens before any model call.
- **Never log** message text outside `LOG_FULL_PROMPTS`, balances, full session ids, tokens, passwords, API keys, or any raw account, mobile or OTP value (last 4 digits only). Customer ids (`CUST-001`) are fine.
- Provider errors log the **HTTP status, error type and the provider's error message**, never the request.
- Internal errors log the exception type, `str(exc)` cut to 200 characters, and the code locations.
- `LOG_LEVEL` (default `INFO`) sets the level.

## 2. Layout

```
HH:MM:SS LEVEL  [t=<turn8> s=<sess6>] <event padded to 14> key=value key=value
```

## 3. Events

| Event | When | Fields |
| --- | --- | --- |
| `request` | each API call | method, path, status, ms |
| `session` | created, wiped, expired | action, reason |
| `route` | turn routed | source=`flow`/`rules`/`router`/`fallback`, intent or tool, verified=yes/no |
| `router_llm` | router model call | model, ms, tool chosen, queries count (summary only), tokens if returned |
| `retrieval` | document search | queries count, chunks, top doc titles, best score, ms |
| `answer_llm` | answer model call | model, ms, prompt_tokens_est, completion tokens if returned, finish reason |
| `tool` | tool run | name, allowed/blocked, customer, rows, ms |
| `verify` | verification step | step reached, result, tries (masked values only) |
| `flow` | flow transition | flow, from step, to step, or completed/cancelled/expired |
| `service` | request created or card blocked | reference, type, customer |
| `guard` | output guard or masking changed something | what, count |
| `limit` | rate limit hit | scope (session/ip), retry_after |
| `task` | background job | name (purge, ingestion), count, ms |
| `provider_err` | Groq or Gemini failure | status, type, message, retry |
| `error` | unexpected failure | type, message, locations |
| `turn_done` | end of every chat turn | route, llm_calls, tools, total_ms |

## 4. Example output

```
12:31:07 INFO   [t=ab12cd34 s=yuZZ06] request        POST /chat 200 ms=412
12:31:07 INFO   [t=ab12cd34 s=yuZZ06] route          source=rules intent=balance verified=no -> verification
12:31:07 INFO   [t=ab12cd34 s=yuZZ06] verify         step=account asked
12:31:21 INFO   [t=c3d4e5f6 s=yuZZ06] verify         step=otp ok mobile=****0101
12:31:21 INFO   [t=c3d4e5f6 s=yuZZ06] tool           get_balances allowed customer=CUST-001 rows=1 ms=18
12:31:21 INFO   [t=c3d4e5f6 s=yuZZ06] turn_done      route=flow llm_calls=0 tools=1 total_ms=64
12:32:02 INFO   [t=0a1b2c3d s=yuZZ06] router_llm     model=openai/gpt-oss-20b ms=640 tool=search_documents queries=2
12:32:03 INFO   [t=0a1b2c3d s=yuZZ06] retrieval      queries=2 chunks=8 top=["Loan products","Fees and charges"] best=0.81 ms=520
12:32:05 INFO   [t=0a1b2c3d s=yuZZ06] answer_llm     model=openai/gpt-oss-120b ms=1310 prompt_tokens_est=2100
12:32:05 INFO   [t=0a1b2c3d s=yuZZ06] turn_done      route=router llm_calls=2 tools=1 total_ms=2570
12:33:10 ERROR  [t=9f8e7d6c s=yuZZ06] provider_err   status=404 type=NotFoundError message="The model `x` does not exist"
```

## 5. Acceptance

- Every turn ends with exactly one `turn_done` line.
- A failed model call shows its status and message.
- A grep of the terminal output for a typed account number, mobile number or OTP finds nothing.
- With `LOG_FULL_PROMPTS=true` the prompts appear at DEBUG; with it off they do not.
- Tests cover masking in logs, the `provider_err` line and the `turn_done` line.