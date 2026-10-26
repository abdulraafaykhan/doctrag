# Decisions

- Phase 1: reserve daily quotas atomically before each provider attempt; failed attempts consume budget conservatively. Upload embedding stops at the lower of the percentage threshold and query-reserved balance.
- Phase 1: existing config lacks an LLM daily budget; use a configurable provisional budget that must be confirmed with provider consoles before live LLM traffic.
- Phase 1: vision callers must locally redact images before setting pii_redacted; quota exhaustion returns masked local OCR with the configured warning.
- Phase 1: startup requires normal indexes and the Atlas vector index to be READY; create indexes explicitly rather than mutate the schema during API startup.
- Phase 1: shared Atlas search capacity is exhausted; use the regular chunks_text index and filtered $text keyword search, leaving existing indexes untouched.
- Phase 1: pyproject dependencies mirror requirements.in; requirements.txt remains the dependency lock. uv add uses frozen mode to avoid a second lockfile.
