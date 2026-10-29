"""Summarize completed evaluations without making generation requests."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import ROOT, get_settings
from app.db import close_db
from app.ingest.store import Store
from app.llm import pacific_day


def main():
    cfg = get_settings()
    offline = json.loads((ROOT / 'eval/gate2_offline.json').read_text())
    retrieval = json.loads((ROOT / 'eval/gate1_context_check.json').read_text())
    results = json.loads((ROOT / 'eval/gate2_live.json').read_text())['results']
    models = []
    for model in cfg.conversation['agent']['bakeoff_models']:
        rows = [row for row in results if row['model'] == model and row['repetition'] == 1 and row['group'] in {'B', 'H'}]
        calls = sum(row['tool_calls'] for row in rows)
        valid = sum(row['valid_tool_calls'] for row in rows)
        missing = sum(sum(error.startswith('missing tool') for error in row['errors']) for row in rows)
        models.append({'model': model, 'cases': len(rows), 'passed': sum(row['passed'] for row in rows), 'tool_calls': calls, 'valid': valid, 'tool_success': valid / max(1, calls + missing), 'failures': [row['id'] for row in rows if not row['passed']]})
    chosen = cfg.models.agent
    selected = [row for row in results if row['model'] == chosen]
    groups = {group: {'passed': sum(row['passed'] for row in selected if row['group'] == group), 'total': sum(row['group'] == group for row in selected)} for group in ['B', 'G', 'H']}
    logical_tokens = sum(usage.get('total_tokens', 0) for row in selected for usage in row['usage'])
    turns = sum(len(row['turns']) for row in selected)
    agent_turns = sum(bool(turn.get('usage')) for row in selected for turn in row['turns'])
    usage = Store().one('quota_usage', {'day': pacific_day()}) or {}
    quotas = {key: usage.get(key, 0) for key in ['embed_requests', 'vlm_requests', 'llm_requests']}
    expected = {'B': 42, 'G': 39, 'H': 9}
    passed = all(groups[group]['total'] == count and groups[group]['passed'] == count for group, count in expected.items())
    chosen_score = next(row for row in models if row['model'] == chosen)
    passed &= chosen_score['tool_success'] >= 0.95
    passed &= offline['passed'] and retrieval['hits'] / retrieval['total'] >= 0.9
    report = {'passed_live_gate': passed, 'chosen_model': chosen, 'models': models, 'groups': groups, 'tokens_total_evaluated': logical_tokens, 'chat_turns': turns, 'agent_turns': agent_turns, 'average_tokens_per_chat_turn': logical_tokens / max(1, turns), 'average_tokens_per_agent_turn': logical_tokens / max(1, agent_turns), 'pacific_day': pacific_day(), 'quota_requests': quotas, 'context_token_budget': cfg.retrieval['context_token_budget'], 'confirmed_tpm': cfg.conversation['agent']['free_tpm'], 'limitations': ['Deterministic OTP, confirmation, visibility and security shortcuts intentionally bypass the LLM even in live scenarios.', 'Live account operations use isolated synthetic customer stores; no real customer actions are submitted to Atlas.', 'Gemini agent fallback is covered with fake failures only, as real Phase 3 calls are restricted to Groq.', 'Token averages include cached logical responses; daily quota counts are actual reserved provider attempts.']}
    (ROOT / 'eval/gate2_report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    lines = ['# Gate 2', '', f'Live release gate passed: {passed}', '', f'Chosen: {chosen}; tool-call success {chosen_score["tool_success"]:.1%}.', '', '| Model | B/H cases passed | Valid tool calls | Tool-call success |', '|---|---:|---:|---:|']
    lines += [f'| {row["model"]} | {row["passed"]}/{row["cases"]} | {row["valid"]}/{row["tool_calls"]} | {row["tool_success"]:.1%} |' for row in models]
    lines += ['', f'Selected-model repetitions: {groups}.', '', f'Average tokens: {report["average_tokens_per_agent_turn"]:.0f} per agent turn; {report["average_tokens_per_chat_turn"]:.0f} across all chat turns. Context budget: {report["context_token_budget"]}; confirmed free-tier limit: {report["confirmed_tpm"]} TPM.', '', f'Pacific day {pacific_day()}: {quotas}.', '', 'Limits: https://console.groq.com/docs/rate-limits ; tool support: https://console.groq.com/docs/tool-use/overview', '', 'Limitations:']
    lines += ['- ' + value for value in report['limitations']]
    lines += ['', f'Offline gate: {offline["tests_passed"]} passed, {offline["tests_skipped"]} opt-in live Gate 1 test skipped. Groups A/C/I/J and deterministic B/G paths use FakeLLM and MemoryStore; the existing Gate 1 resume test covers I6.', '', 'Step 0: the original 24 cell mismatches were confined to flagged images. Both targeted retries now match all 12 expected cells each; cut-off rows remain null and excluded. Complete-row lookup passes in isolation, and the Atlas 12-month general FD row remains 7.10%.', '', f'Reduced-context retrieval check: {retrieval["hits"]}/{retrieval["total"]} top-5 hits at {retrieval["context_token_budget"]} context tokens, with {retrieval["embedding_requests"]} new embedding calls.', '', 'Development retries are included in daily quota counters; the comparison table describes the final evaluated cases. Invalid proposals rejected before execution are included in the tool-call denominator.']
    (ROOT / 'eval/gate2_report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)
    return passed


if __name__ == '__main__':
    try:
        raise SystemExit(0 if main() else 1)
    finally:
        close_db()
