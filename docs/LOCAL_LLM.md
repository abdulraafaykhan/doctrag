# Local model operation

Current settings, verified by reading code; this cleanup made no live provider requests.
Configuration is cached by get_settings: restart the backend after edits.

## Current endpoint and budgets

Secrets.local_llm_url in app/config.py reads optional LOCAL_LLM_URL. The example file leaves
it empty; supply your inference URL privately. LocalClient.request in app/local_llm.py posts:

```json
{"server":"161","prompt":"one string","max_tokens":400}
```

The server response uses top-level response (text), prompt_tokens, completion_tokens;
additional timing/total fields may be present. local_llm.text_field selects response.
Images use a real optional image string, configured as a data URL; there is no messages/tool
array in the server payload. app.llm.LLMClient._vision passes the redacted image separately.
No new endpoint/schema/vision capability check was performed during cleanup.

| Setting | Current value | Reader |
|---|---|---|
| models.agent | local/default | Runner.run, Chat._turn, Registry._model_tools |
| models.agent_fallbacks | [] | Runner.run |
| models.vlm_ingest / vlm_online | local/default / local/default | LLMClient.vision |
| models.vlm_ingest_fallbacks / vlm_online_fallbacks | [] / [] | LLMClient.vision |
| local_llm.server_id | "161" | LocalClient.request |
| local_llm.max_tokens / max_tokens_tool | 400 / 200 | LocalClient.request |
| local_llm.timeout_seconds | 25 | LocalClient.request |
| local_llm.text_field | response | LocalClient.request |
| local_llm.protocol_retries | 1 (two attempts total) | LocalClient.chat |
| local_llm.context_limit_tokens / max_prompt_chars | 6500 / 16000 | LocalClient.fits/prompt |
| local_llm.history_token_budget | 300 | Runner.run, LocalClient.prompt |
| local_llm.retrieval.context_token_budget / parents_to_model | 800 / 3 | LocalClient.prompt |
| local_llm.stop_markers | newline User:, Assistant:, Human:, Customer: | LocalClient.parse/write |
| local_llm.image_token_cost | 1429 (observed 1555 server tokens minus 126 text estimate) | LocalClient.request |
| local_llm.raw_output_chars / error_text_chars | 200 / 120 | LocalClient.reject, Runner._provider_error |
| local_llm.document_reference_words | above/this/attached/image/screenshot/file/document/pdf and configured variants | Registry.references_upload |
| local_llm.balance_words | balance, balances | Chat._turn |
| local_llm.routing_passthrough_words | configured human/auth/fraud/lost/stolen words | Chat._turn |
| local_llm.enabled_tools | searches, auth, balance, cheque, flow status/summary/cancel, escalation | Registry._model_tools |
| local_llm.direct_display_tools | cheque, flow status/summary/cancel, balance, auth | Runner._fake |
| local_llm.scope_instruction / tool_result_instruction / final_reply_instruction | configured local scope and loop/final instructions | system_prompt, Runner._fake, LocalClient.chat |
| local_llm.image_encoding | data_url | LLMClient._vision |

All local_llm/models settings above are defined in config/settings.yaml and loaded into
Settings in app/config.py. LOCAL_LLM_URL defaults to None if absent. Secrets still requires
GROQ_API_KEY, GEMINI_API_KEY and MONGODB_URI even when text and vision are local.
models.embed is unchanged: Gemini embeddings can still use remote quota during RAG/uploads.
Local HTTP agent calls are token-metered but bypass the remote LLM request quota reservation;
vision enters LLMClient._attempt and its quota guard. Do not equate those counters.

## Routing and answer writing

API.turn → API._metered_turn/turn_locked → Chat.turn_locked → Chat._turn.
Exact greetings and auth/flow fast paths can finish without any model request.
For local mode, code routes current attachments or references to session uploads to
search_my_documents; otherwise qualifying public questions with no personal intent/open flow
go to search_bank_knowledge; otherwise verified balance words select get_balance if visible.
Identity-bearing messages and an awaiting-OTP code retain the existing authentication path.
Auth/escalation passthrough words prevent the public route from swallowing those requests.

Chat._agent → Runner.run(code_route=...) → Registry.tools[name].invoke → BoundTool.invoke.
The actual customer's masked message is the search query. Tool visibility/schema checks,
audit writes and TOOL spans remain in code. Selected paths log rag-code-routed with why.
search_knowledge in app/rag.py filters active namespace/owner data, obtains vector/text
candidates and RRF-merges them; parent section text becomes returned items. BoundTool.invoke
assigns source numbers and adds conversation.agent.source_wrapper, containing [SOURCE n | ...].
The global retrieval budget is 1200 tokens; the local prompt imposes its additional 800/3 caps.
No-good-match/empty search skips the model and returns messages.answers.not_found, offering a human.
get_balance returns its exact code display without a model call. Other actions keep the local loop.

For successful search, Runner.run → LocalClient.write → prompt(writer=True) → request → httpx POST.
The prompt contains existing system rules, bounded history, wrapped retrieved sources and the
question, ending in prompts/local_writer.txt. It contains no tool schemas/JSON tool protocol.
write cuts configured dialogue suffixes and returns plain text. The HTTP output limit is 400.
History is trimmed first, then lowest-scored sections; over-limit prompts fail rather than
silently exceed the token/character caps. Image calls add configured image-token cost.
Prompt estimates and server-reported prompt_tokens are logged separately.

Runner.run adds missing tool displays, canonicalizes citations and calls app.verify.check.
Numeric units and source numbers must match evidence. One repair may run via Runner._native
and LocalClient.chat(plain=True); after failure use the configured safe/retry response.
This keeps the existing verifier/repair behavior; a writer turn may therefore include its one repair.

## Remaining local tool-loop path

Chat._agent → Runner.run → Runner._native local branch → Runner._fake → LocalClient.chat.
Registry.visible calculates auth/flow visibility, then local enabled_tools intersects it.
Upload references additionally hide bank search. Disabling a tool never removes its code;
editing enabled_tools restores it only when the normal visibility rules allow it.
system_prompt uses agent_system/agent_blocks plus bank/date/auth/flow/file blocks unchanged,
then adds the local scope instruction. LocalClient.prompt flattens schemas/history/messages
into prompts/local_tool_protocol.txt, ending Assistant (one JSON object only):.

LocalClient.parse cuts stop markers, strips fences and raw_decodes the first JSON object;
trailing invented text is ignored. It accepts either nonempty reply text or valid nonempty
tool_calls. Invalid/empty calls reject the entire output, even if reply is present. Valid
calls take precedence over reply. Names must be visible, arguments must pass args_schema;
model-proposed identity keys are recursively discarded. An unrelated search query is replaced
with the customer's message. Raw rejected output is masked/truncated in raw_output, not reason.
One retry follows protocol rejection, then LocalLLMError. HTTP errors/timeouts/missing URL also raise.

Runner._fake invokes validated tools, appends results and an answer-now instruction. Repeating
the same name/arguments triggers one final request with no tool list. Successful configured
direct-display tools finish immediately. agent.max_steps=4 and max_tool_calls=6 bound work.
The model cannot confirm/submit; those operations remain plain code.

## Tokens, failures and provider switch

LocalClient.request records input/output with TokenMeter; missing fields are estimated via
ceil(chars/4) and marked estimated. Embeddings are excluded. API/UI expose session totals,
while quota_usage tracks a different Pacific-day request ledger. Cached/fake calls cost zero.
Runner._provider_error logs actual exception class, HTTP status and server error prefix;
empty agent_fallbacks means only the primary runs, then answers.retry_later. No Groq/Gemini
fallback is called in the current local configuration. Vision also has no provider fallbacks;
its failure can return masked local OCR with needs_retry. Local vision uses the 25s local
HTTP timeout even though the remote ingest timeout setting remains 90s.

To restore the full Groq agent path, edit only this models line in config/settings.yaml:

```yaml
  agent: "groq/qwen/qwen3.8-27b"
```

Keep agent_fallbacks: [] for Groq-only execution. To restore the former remote fallback pair:

```yaml
  agent_fallbacks: [groq/openai/gpt-oss-20b, gemini/gemini-flash-lite-latest]
```

The native Groq endpoint is https://api.groq.com/openai/v1. Strip only the outer groq/
routing prefix: qwen/qwen3.8-27b and openai/gpt-oss-20b remain intact. LiteLLM is not required.
This disables local-only routing/tool filtering. It does not change vision or embedding settings.
Optional restoration of the former separate vision providers is:

```yaml
  vlm_ingest: "gemini-3.5-flash"
  vlm_ingest_fallbacks: [groq/qwen/qwen3.8-27b]
  vlm_online: "groq/qwen/qwen3.8-27b"
  vlm_online_fallbacks: [gemini-3.5-flash]
```

Verify provider availability/quota before authorized live use; these are recorded IDs, not a
fresh availability check. No files need removal to revert. Removing the adapter itself would
require removing all imports/branches in agent.py and llm.py first; do not simply delete it.

## Decision and historical record

The following appended record preserves original decision rationale and the earlier server-154
change map, probe body, failures, usage and trace trees. It is historical, not the current setup.
Current settings/routing above supersede old model/fallback/protocol descriptions. Historical
passing checks are not checks run during this cleanup. The most recent code-routed screenshot
attempt returned no_good_match via keyword-only retrieval, then failed on an unsupported log.path
keyword; that keyword was fixed afterward without repeating the live turn. The writer therefore
remains unverified end to end. Inspect data/local_writer_live.txt for that actual failure.

### Original decision record (superseded values are historical)

# Decisions

- Phase 1: reserve daily quotas atomically before each provider attempt; failed attempts consume budget conservatively. Upload embedding stops at the lower of the percentage threshold and query-reserved balance.
- Phase 1: existing config lacks an LLM daily budget; use a configurable provisional budget that must be confirmed with provider consoles before live LLM traffic.
- Phase 1: vision callers must locally redact images before setting pii_redacted; quota exhaustion returns masked local OCR with the configured warning.
- Phase 1: startup requires normal indexes and the Atlas vector index to be READY; create indexes explicitly rather than mutate the schema during API startup.
- Phase 1: shared Atlas search capacity is exhausted; use the regular chunks_text index and filtered $text keyword search, leaving existing indexes untouched.
- Phase 1: pyproject dependencies mirror requirements.in; requirements.txt remains the dependency lock. uv add uses frozen mode to avoid a second lockfile.
- Live check: LiteLLM is absent; native CrewAI uses https://api.groq.com/openai/v1 and preserves openai/gpt-oss-120b or openai/gpt-oss-20b after stripping only the groq/ routing prefix (CrewAI otherwise strips openai/ too).
- Vision benchmark: select pinned gemini-3.5-flash for exact rate cells and conservative nulls on cut-off rows; use groq/qwen/qwen3.8-27b on 429, 5xx or timeout, then masked local OCR if all vision providers fail.
- Vision modes: ingest uses Gemini then Groq at 90 s; online uses Groq then Gemini at 25 s; require JSON with one parse retry, OCR-backed notes/numeric tokens, and null cut-off rows from model flags or caller-supplied zero-based crop geometry.
- Phase 2: sample ingestion stores parsed chunks once with embeddings deferred; Gate 1 alone makes real embedding requests, and resumptions fill only missing vectors using both test-cache and MongoDB caches.
- Phase 2: image hashes use normalized pixels to deduplicate container encodings; bottom-edge ink identifies clipped rows, which are null and review-flagged even when a provider omits them.
- Phase 2: extraction gate counts unreadable provider responses as flagged missing cells, never successful extraction; a passed safety gate does not imply every image was readable.
# Phase 3: native CrewAI uses Groq's OpenAI-compatible endpoint with complete native model IDs (openai/ for GPT-OSS, qwen/ for Qwen); schemas retain Pydantic optional fields, structured masking precedes JSON serialization, and real B/G/H results are cached and paced at the confirmed 8,000 TPM.
# Retryable vision failures remain needs_retry with a cooldown; independently OCR-validated complete rows remain eligible for exact lookup while cut-off/null rows are excluded.
# Gate 2 selects Groq Qwen 3.8 27B: count every model-proposed tool call, including pre-execution schema failures; native CrewAI preserves qwen/ for Qwen and openai/ for GPT-OSS, with no LiteLLM dependency.
- Phase 4: HttpOnly same-site session cookies bind file ownership; tab IDs bind chat actions; one session lease covers the turn and retry transcript, and UI transcripts mask credentials independently of model history.
- Phase 4: browser checks use existing Playwright plus installed Edge with FakeLLM; live HTTP/Atlas/Groq results remain separately cached, including failures, and uncached cases use the configured Groq fallback when the primary daily token bucket is exhausted.
- Phase 4: policy questions remain public despite cheque/card/statement keywords; numeric validation equates INR/₹ and percent/% while preserving currency-versus-percentage checks.

### Original local adapter change record (server 154; not current configuration)

# Local text model adapter

## a. Changed files
| File path | What changed | Function or section |
|---|---|---|
| .env.example | Optional endpoint example; no credentials added | LOCAL_LLM_URL |
| app/config.py | Optional endpoint and loaded local settings | Secrets, Settings |
| config/settings.yaml | Local primary, three existing fallbacks, server settings | models, local_llm |
| app/local_llm.py | Flattened prompt, strict JSON/tool validation, HTTP and usage | LocalClient.prompt/request/parse/chat, LocalLLMError |
| app/agent.py | Reuse the tool loop for local calls; local errors enter fallback | Runner._fake, Runner._native, Runner.run |
| app/usage.py | Mark estimated counts in events and per-call results | TokenMeter.add |
| prompts/local_tool_protocol.txt | Visible tool schemas and the two permitted JSON forms | Entire prompt |
| tests/test_local_llm.py | Mock HTTP, validation, repair, usage and fallback checks | test_valid_tool_then_reply, test_local_failure_falls_back |
| _cleanup_trash/scripts/check_local_llm.py | Local-only live pair with existing rates and captured responses | main |
| docs/LOCAL_LLM_CHANGES.md | This change map, results and pasted trace trees | Entire document |

## b. Settings and variables
| Setting | Defined in | Read in | Default |
|---|---|---|---|
| LOCAL_LLM_URL | .env.example; optional Secrets.local_llm_url in app/config.py | LocalClient.request in app/local_llm.py | None; example http://192.168.0.109:8000/api/inference |
| local_llm | config/settings.yaml; Settings in app/config.py | LocalClient.__init__ | Required section below |
| local_llm.server_id | config/settings.yaml | LocalClient.request | "154" |
| local_llm.max_tokens | config/settings.yaml | LocalClient.request | 768 |
| local_llm.timeout_seconds | config/settings.yaml | LocalClient.request | 25 seconds |
| local_llm.text_field | config/settings.yaml | LocalClient.request | response |
| local_llm.protocol_retries | config/settings.yaml | LocalClient.chat | 1 |
| models.agent | config/settings.yaml | Runner.run in app/agent.py | local/default |
| models.agent_fallbacks | config/settings.yaml | Runner.run | groq/qwen/qwen3.8-27b, groq/openai/gpt-oss-20b, gemini/gemini-flash-lite-latest |
Set LOCAL_LLM_URL in your environment or your own .env; .env was neither read directly nor edited. Missing URL raises LocalLLMError and allows fallback. Settings load it through pydantic-settings. Changing models.agent selects the provider; restart the backend after config/environment edits because get_settings is cached.

## c. How a user message reaches the local model
app/chat.py Chat._turn -> Chat._agent -> app/agent.py Runner.run -> Runner._native local branch -> Runner._fake with LocalClient -> app/local_llm.py LocalClient.chat -> LocalClient.prompt -> LocalClient.request -> httpx.Client.post.
Runner.run supplies system_prompt(session, registry) and a user task containing bounded history and the current message. LocalClient.prompt inserts that transcript and Registry.list() tool names, descriptions and args_schema JSON into prompts/local_tool_protocol.txt, then ends with Assistant:. Only one prompt string goes over HTTP, with server and max_tokens; no messages or tools fields are sent. mask_text and the MOBILE/ACCOUNT scan remain at the boundary.

## d. How tool calls work
Registry.visible and Registry.list in app/tools/registry.py select session-appropriate tools. LocalClient.parse uses json.loads, requires exactly reply or tool_calls, rejects unknown/hidden tools and validates every call with that tool's args_schema.model_validate before any tool runs. It recursively discards user_id, account_id and account_number supplied by the model. Other invalid arguments are rejected, not silently accepted.
Runner._fake invokes BoundTool.invoke, appends each result as a tool transcript entry, and sends it back through LocalClient.prompt on the next turn. Registry.direct_display can finish immediately for exact display tools. agent.max_steps=4 bounds the loop; BoundTool.invoke enforces agent.max_tool_calls=6. Bad protocol gets one retry with the invalid response in context, then LocalLLMError.

## e. How the final reply is produced
LocalClient.parse turns {"reply":"..."} into a string; Runner._fake returns it. Runner.run adds missing tool display text, calls canonical_citations and check from app/verify.py for number/citation validation, and uses the existing single repair path when needed. For local repair, Runner._native calls LocalClient.chat(plain=True): no tool protocol or execution, just the existing repair prompt flattened to text. The Groq/Gemini native branches and caller-side verification/repair logic are unchanged.
LocalClient.request reads top-level prompt_tokens and completion_tokens. If either is absent, that part is estimated using ceil(chars / chunking.chars_per_token), currently chars/4. TokenMeter.add records estimated=True in its event and per-call log result; session totals retain their existing shape. Embeddings are not counted here.

## f. How the RAG pipeline feeds it
Registry._build binds search_bank_knowledge to app/rag.py search_knowledge; search_my_documents calls the same function with namespace=user and the bound owner_key. search_knowledge reads filtered chunks using vector/text retrieval, rrf merges candidates, and parent sections supply context. retrieval.context_token_budget in config/settings.yaml is 1200. search_knowledge assigns item n values; BoundTool.invoke renumbers them across calls and wraps source text using conversation.agent.source_wrapper. Its returned envelope enters the next local prompt as Tool: text, including citation evidence. No RAG, embedding or ingestion implementation changed.

## g. Fallback
Order: local/default -> groq/qwen/qwen3.8-27b -> groq/openai/gpt-oss-20b -> gemini/gemini-flash-lite-latest. LocalClient.request wraps HTTP errors (including timeout, connection errors and 5xx), invalid response envelopes and a missing URL in LocalLLMError. LocalClient.chat raises the same error after two invalid JSON/tool outputs. Runner.run logs provider failed and fallback switch, then tries the next configured model. Native provider fallback handling is otherwise unchanged.
Offline checks: tests/test_local_llm.py passed 5 cases; tests/test_gate2.py -k agent passed 1 case (40 deselected). Fallback providers were stubbed; no live Groq/Gemini calls occurred.
Probe: HTTP 200, {"response":"Ok! How may I assist you today?","latency_seconds":0.1738597479998134,"prompt_tokens":34,"completion_tokens":10,"total_tokens":44,"tokens_per_second":57.52}.
Live checks used server 154, verified synthetic sessions and existing Atlas rate rows copied into MemoryStore. Searches had no chunks, so could not invoke an embedding API. An explicit Runner(model='local/default') prevented external fallbacks during the check. One prompt-layout adjustment was made; no further tuning followed.
FD: four valid lookup_rate calls parsed and executed, but the model repeated them until max_steps and did not produce a final rate answer. Cheque book: malformed/repeating JSON exhausted both attempts; zero tools executed. The local model did not complete both tasks successfully. Final pair usage: FD in=12059/out=143; cheque in=6284/out=1536, all server-reported. The initial live capture hit a Windows encoding error; the checker was rerun with PYTHONUTF8=1, without changing logging.
Full raw local replies: data/local_llm_check.json. Both actual final trace trees are pasted below (wrapped terminal lines joined).

## h. How to revert
Change config/settings.yaml to exactly: `agent: "groq/qwen/qwen3.8-27b"` under models. No file removal is needed to switch back; the local adapter can remain unused. The same Groq ID also remains first in agent_fallbacks, so remove that duplicate fallback entry if you want the original two-entry fallback list.
For full removal, first undo the local branch/loop additions and LocalLLMError fallback hook in app/agent.py; then remove app/local_llm.py, prompts/local_tool_protocol.txt, tests/test_local_llm.py, _cleanup_trash/scripts/check_local_llm.py and this document. Remove local_llm from Settings/config, local_llm_url from Secrets, and LOCAL_LLM_URL from .env.example/your environment. TokenMeter's optional estimated flag is harmless to retain or can be removed with its callers. Do not delete the adapter while Runner.run still imports LocalLLMError.

```text
TRACE TREE r-9c58  local check total=2.62s db=0.000s (0 ops)
└─ AGENT app.agent.Runner.run ok 2.616s name=agent turn in=12059 out=143  total=12202
   ├─ AGENT app.agent.Runner._native ok 2.605s name=native agent in=12059  out=143 total=12202
   │  ├─ LLM app.agent.Runner._fake ok 0.661s name=local tool turn  model=local/default in=2762 out=29 total=2791
   │  │  └─ LLM app.local_llm.LocalClient.request ok 0.639s name=local text  request model=local/default in=2762 out=29 total=2791
   │  ├─ TOOL app.tools.registry.BoundTool.invoke ok 0.000s name=lookup_rate 
   │  ├─ LLM app.agent.Runner._fake ok 0.606s name=local tool turn  model=local/default in=2930 out=38 total=2968
   │  │  └─ LLM app.local_llm.LocalClient.request ok 0.595s name=local text  request model=local/default in=2930 out=38 total=2968
   │  ├─ TOOL app.tools.registry.BoundTool.invoke ok 0.000s name=lookup_rate 
   │  ├─ LLM app.agent.Runner._fake ok 0.694s name=local tool turn  model=local/default in=3099 out=38 total=3137
   │  │  └─ LLM app.local_llm.LocalClient.request ok 0.673s name=local text  request model=local/default in=3099 out=38 total=3137
   │  ├─ TOOL app.tools.registry.BoundTool.invoke ok 0.001s name=lookup_rate 
   │  ├─ LLM app.agent.Runner._fake ok 0.628s name=local tool turn  model=local/default in=3268 out=38 total=3306
   │  │  └─ LLM app.local_llm.LocalClient.request ok 0.616s name=local text  request model=local/default in=3268 out=38 total=3306
   │  └─ TOOL app.tools.registry.BoundTool.invoke ok 0.000s name=lookup_rate 
   └─ VERIFY app.verify.check ok 0.001s name=number check 
TRACE TREE r-4533  local check total=20.98s db=0.000s (0 ops)
└─ AGENT app.agent.Runner.run ok 20.976s name=agent turn in=6284 out=1536  total=7820
   └─ AGENT app.agent.Runner._native error 20.973s name=native agent in=6284  out=1536 total=7820
      └─ LLM app.agent.Runner._fake error 20.972s name=local tool turn  model=local/default in=6284 out=1536 total=7820
         ├─ LLM app.local_llm.LocalClient.request ok 10.364s name=local text  request model=local/default in=2757 out=768 total=3525
         └─ LLM app.local_llm.LocalClient.request ok 10.581s name=local text  request model=local/default in=3527 out=768 total=4295
```
