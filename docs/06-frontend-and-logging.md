# 06 Frontend and terminal logging

## Frontend stack
React 19 + Vite (`react@19.3.1`, `react-dom@19.3.1`, `vite@8.3.5`, `@vitejs/plugin-react@6.1.2`) and `react-markdown@10.1.0` + `remark-gfm@4.0.1` for tables in replies. Node 20+ (22 is installed in the dev environment). Plain CSS file, no UI framework, no CSS-in-JS. Commit the lockfile. Dev: `npm run dev` on 5173 with a proxy to the API on 8000. Production: `npm run build`, FastAPI serves `frontend/dist` at `/`.

## Visual rules
- Very simple, clean, minimal. One centered column (max width ~760px), white/near-white background, one accent colour, system font. Light and dark via `prefers-color-scheme`.
- Header: bank name, a small "new chat" button, a login status chip ("Not verified" / "Verified as <first name>").
- **No overlapping elements, no clipping.** Use normal flow, flexbox or grid, no absolute positioning for layout, no fixed heights that cut text. Test at 360px and 1280px width.
- Messages list scrolls; the input bar stays at the bottom. Long words and tables wrap or scroll inside their own container.
- **The customer's sent message is always shown** immediately (right aligned bubble) with its text and its attachment cards, before the reply arrives. If sending fails, keep the message and show a "Retry" control.
- A "thinking" indicator while waiting; disabled send button while a request is in flight.
- Replies (left): rendered markdown; citations `[1]` are small clickable chips that open a side-free popover list or scroll to a "Sources" list under the reply (title, page, version, date).
- Option buttons for the open flow (e.g. 20 / 40 / 60; Emergency / Normal; Confirm / Change / Cancel) appear under the latest reply as a row that wraps; clicking one sends it as the customer's message (shown as a normal sent message).
- OTP step: reply says "Enter the 6-digit code". Show a quiet note that this is a demo where any 6 digits work.
- Honest status lines: "Reading your image...", "Processing document (3 of 12 pages)...".

## Attachments (the user's explicit requirements)
- Attach button and drag-drop in the input bar. Max 5 files per message in the UI; show size and type errors immediately (allowed: pdf, pptx, docx, xlsx, csv, md, txt, png, jpg, jpeg, webp).
- **Before sending:** each pending file appears as a card above the input (image thumbnail or file icon, name, size) with an **x remove button** that removes it locally and tells the backend to delete any early upload. Send is allowed with files only.
- **After sending:** the same cards stay on the sent message. Clicking a card opens a preview: images in an in-page viewer, PDFs and text in a new tab, other types as a download. Files are served from `GET /api/files/{attachment_id}` for the owning session only (404 otherwise).
- Cards show a status chip: Uploading, Processing, Ready, Partial, Failed (with the reason). Files expire after 24 h; an expired card shows "expired" and stays in the list.
- Reloading the page restores the conversation and its cards from `GET /api/session`.

## API routes
- `POST /api/session` create/restore session; returns session_id, history, attachments, auth chip state, open flow options. Registers the tab (`client_id`); a second tab gets a 409 and the UI shows "This chat is open in another tab".
- `POST /api/chat` body `{session_id, client_id, message, attachment_ids[]}` -> `{reply_markdown, citations[], options[], auth_state, flow_state, attachments[], notices[]}`
- `POST /api/upload` multipart (one file) -> `{attachment_id, name, mime, size, kind, status}`; processing continues in the background.
- `GET /api/upload/{attachment_id}` status poll (UI polls every 2 s while Processing).
- `DELETE /api/upload/{attachment_id}` remove (before send, or later from the document list).
- `GET /api/files/{attachment_id}` serve the original for preview.
- `POST /api/ui-event` body `{session_id, event, detail}` for every button click and UI action; the backend only logs it (see below). Failures here are silent for the user.
- `GET /api/health` DB, indexes, quota remaining, models configured.
- Errors: JSON `{error, message}`; the UI shows `message` inline, never a stack trace.

## Terminal logging (backend, `rich`)
One module `app/log.py`. Every line: `HH:MM:SS  AREA  action | key=value ...` with a colour per area. Level via `LOG_LEVEL` in `.env`. Never log PII: mobile and account numbers show as `98****21` / `****5511`, OTPs and card data never appear, message text is truncated to 80 chars after masking.

Areas and what must be logged (not exhaustive, but nothing important may be silent):
| Area | Log on |
|---|---|
| UI | every button click, option click, attach, remove, send, new chat, citation open (via `/api/ui-event`) |
| API | every request: route, status, duration |
| SESSION | create/restore, state changes (auth, flow), timeouts |
| AUTH | candidate captured, pair check result (no detail on which part), OTP issued/verified/failed, lock |
| PREPASS | regex hits (types only, not values), masking applied |
| AGENT | turn start, tools offered (names), each step, each tool call (name, masked args, result code, ms), final answer length |
| TOOL | one line per tool with code and latency |
| FLOW | slot set, step changes, summary created, confirm, submit (ref no), cancel |
| LLM | provider, model, purpose, input/output tokens when known, latency, fallback taken, 429s |
| EMBED | batch size, cache hits/misses, requests used today vs budget |
| VLM | image hash prefix, crop size, model, latency, cache hit |
| OCR | engine (RapidOCR), image hash prefix, text blocks, mean confidence, ms |
| PARSE | file name, type, library used (pymupdf / pdfplumber / python-pptx / ...), pages, tables found |
| INGEST | job stage changes, chunks done/total, pause/resume on quota |
| RAG | filters, candidates from vector and keyword, merge result, rerank on/off, top scores, `no_good_match` |
| VERIFY | number check pass/fail, repair attempt, safe message used |
| DB | slow queries (> 300 ms) and write operations on important collections |
| UPLOAD | received, validated, accepted/rejected with reason, limits applied |
| CLEANUP | expired sessions/uploads removed |

Provide `log.step(area, action, **kv)`, a `@traced(area, action)` decorator that adds timing and errors, and a context manager `with log.span(area, action):`. Every external call (Groq, Gemini, pymongo write/search, OCR, parser) must use one of them. Also write the same lines (JSON per line, masked) to `data/logs/app.log` with rotation.

Example output:
```
10:42:11  UI       click send | session=ab12 attachments=1
10:42:11  UPLOAD   accepted rates.png | 214KB image/png
10:42:12  OCR      RapidOCR | img=9f2c blocks=31 conf=0.93 ms=640
10:42:12  VLM      skipped (clear text) | img=9f2c
10:42:12  AGENT    turn start | tools=8 state=ANONYMOUS
10:42:13  TOOL     search_bank_knowledge | code=OK hits=5 ms=420
10:42:13  RAG      vector=20 keyword=20 merged=6 top=0.81
10:42:14  LLM      groq gpt-oss-120b | purpose=answer in=2840 out=190 ms=1310
10:42:14  VERIFY   numbers ok | 3 checked
```
