# Gate 2

Live release gate passed: True

Chosen: groq/qwen/qwen3.8-27b; tool-call success 100.0%.

| Model | B/H cases passed | Valid tool calls | Tool-call success |
|---|---:|---:|---:|
| groq/openai/gpt-oss-20b | 22/23 | 8/9 | 88.9% |
| groq/openai/gpt-oss-120b | 21/23 | 7/9 | 77.8% |
| groq/qwen/qwen3.8-27b | 23/23 | 7/7 | 100.0% |

Selected-model repetitions: {'B': {'passed': 42, 'total': 42}, 'G': {'passed': 39, 'total': 39}, 'H': {'passed': 9, 'total': 9}}.

Average tokens: 5076 per agent turn; 1302 across all chat turns. Context budget: 1200; confirmed free-tier limit: 8000 TPM.

Pacific day 2026-10-26: {'embed_requests': 101, 'vlm_requests': 41, 'llm_requests': 191}.

Limits: https://console.groq.com/docs/rate-limits ; tool support: https://console.groq.com/docs/tool-use/overview

Limitations:
- Deterministic OTP, confirmation, visibility and security shortcuts intentionally bypass the LLM even in live scenarios.
- Live account operations use isolated synthetic customer stores; no real customer actions are submitted to Atlas.
- Gemini agent fallback is covered with fake failures only, as real Phase 3 calls are restricted to Groq.
- Token averages include cached logical responses; daily quota counts are actual reserved provider attempts.

Offline gate: 97 passed, 1 opt-in live Gate 1 test skipped. Groups A/C/I/J and deterministic B/G paths use FakeLLM and MemoryStore; the existing Gate 1 resume test covers I6.

Step 0: the original 24 cell mismatches were confined to flagged images. Both targeted retries now match all 12 expected cells each; cut-off rows remain null and excluded. Complete-row lookup passes in isolation, and the Atlas 12-month general FD row remains 7.10%.

Reduced-context retrieval check: 29/30 top-5 hits at 1200 context tokens, with 0 new embedding calls.

Development retries are included in daily quota counters; the comparison table describes the final evaluated cases. Invalid proposals rejected before execution are included in the tool-call denominator.
