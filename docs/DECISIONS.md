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
