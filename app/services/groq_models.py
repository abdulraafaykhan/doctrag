"""Groq-specific capabilities shared by routing and answer generation."""

GPT_OSS_MODELS = frozenset({"openai/gpt-oss-20b", "openai/gpt-oss-120b"})


def reasoning_options(model: str) -> dict:
    # GPT-OSS uses include_reasoning, not reasoning_format; keep reasoning out of replies.
    # Low effort respects the existing small completion budgets; it does not disable reasoning.
    if model in GPT_OSS_MODELS:
        return {"reasoning_effort": "low", "include_reasoning": False}
    return {}
