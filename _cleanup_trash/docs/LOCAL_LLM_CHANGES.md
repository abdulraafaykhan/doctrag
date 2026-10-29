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
| scripts/check_local_llm.py | Local-only live pair with existing rates and captured responses | main |
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
For full removal, first undo the local branch/loop additions and LocalLLMError fallback hook in app/agent.py; then remove app/local_llm.py, prompts/local_tool_protocol.txt, tests/test_local_llm.py, scripts/check_local_llm.py and this document. Remove local_llm from Settings/config, local_llm_url from Secrets, and LOCAL_LLM_URL from .env.example/your environment. TokenMeter's optional estimated flag is harmless to retain or can be removed with its callers. Do not delete the adapter while Runner.run still imports LocalLLMError.

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
