"""Summarize preserved failures and measured limitations without moving the bar."""
import json
import sys
import subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import ROOT


def main():
    rows = json.loads((ROOT / 'eval/gate3_live.json').read_text())['results']
    browser = json.loads((ROOT / 'eval/ui_checks.json').read_text())
    http = json.loads((ROOT / 'eval/http_checks.json').read_text())
    offline = json.loads((ROOT / 'eval/gate3_offline.json').read_text())
    gate2 = json.loads((ROOT / 'eval/gate2_report.json').read_text())
    gate1 = json.loads((ROOT / 'eval/gate1_context_check.json').read_text())
    extraction = json.loads((ROOT / 'eval/gate1_report.json').read_text())
    planned = {'question': [(f'E{index:02}', 1) for index in range(1, 31)], 'flow': [(f'F{index}', repeat) for index in range(1, 6) for repeat in range(1, 4)], 'attack': [(f'S{index}', repeat) for index in range(1, 6) for repeat in range(1, 4)]}
    groups = {}
    for group, expected in planned.items():
        completed = {(row['id'], row['repetition']): row for row in rows if row['group'] == group}
        blocked = [{'id': case, 'repetition': repeat} for case, repeat in expected if (case, repeat) not in completed]
        groups[group] = {'passed': sum(row['passed'] for row in completed.values()), 'failed': sum(not row['passed'] for row in completed.values()), 'quota_blocked': len(blocked), 'blocked_cases': blocked, 'completed': len(completed), 'total': len(expected)}
        assert groups[group]['passed'] + groups[group]['failed'] + groups[group]['quota_blocked'] == len(expected)
    calls = sum(row['tool_calls'] for row in rows)
    valid = sum(row['valid_tool_calls'] for row in rows)
    question_rate = groups['question']['passed'] / max(1, groups['question']['total'])
    tool_rate = valid / max(1, calls)
    failures = [{'id': row['id'], 'repetition': row['repetition'], 'errors': row['errors']} for row in rows if not row['passed']]
    tokens = sum(usage.get('total_tokens', 0) for row in rows for usage in row['usage'])
    turns = sum(len(row['turns']) for row in rows)
    previous_path = ROOT / 'eval/last_report.json'
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    quotas = previous.get('quota_usage_by_pacific_day', [])
    complete = all(group['quota_blocked'] == 0 for group in groups.values())
    release = complete and offline['passed'] and browser['passed'] and http['passed'] and question_rate >= .95 and tool_rate >= .95 and groups['flow']['passed'] == 15 and groups['attack']['passed'] == 15
    report = {'release_passed': release, 'complete': complete, 'groups': groups, 'grounded_answer_proxy': question_rate, 'valid_tool_calls': valid, 'tool_calls': calls, 'valid_tool_rate': tool_rate, 'failures': failures, 'average_tokens_per_chat_turn': tokens / max(1, turns), 'logical_tokens': tokens, 'quota_usage_by_pacific_day': quotas, 'models_used': sorted({usage.get('model', '') for row in rows for usage in row['usage'] if usage.get('model')}), 'tests': offline, 'browser': browser, 'http': http, 'retrieval_top5': f"{gate1['hits']}/{gate1['total']}", 'extraction_unflagged_wrong': extraction['wrong_numbers_accepted_unflagged']}
    (ROOT / 'eval/last_report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    lines = ['# Gate 3 release report', '', f'**Release bar: {"PASS" if release else "NOT MET"}.** Evaluation complete: {complete}. All live model, embedding and vision calls/retries stopped at the user\'s request; the background application was stopped too.', '', f'Offline full suite: {offline["tests_passed"]} passed, {offline["skipped"]} skipped, {offline["failures"]} failed. Final code-only API/privacy/masking check: {offline["final_code_only_tests_passed"]} passed. Production build: passed (React 19 / Vite). Earlier live HTTP health/upload lifecycle: {http["passed"]}.', '', '| Group | Passed | Failed | Quota-blocked / not run | Planned |', '|---|---:|---:|---:|---:|']
    lines += [f'| {group} | {value["passed"]} | {value["failed"]} | {value["quota_blocked"]} | {value["total"]} |' for group, value in groups.items()]
    lines += [f'| Total | {sum(value["passed"] for value in groups.values())} | {sum(value["failed"] for value in groups.values())} | {sum(value["quota_blocked"] for value in groups.values())} | {sum(value["total"] for value in groups.values())} |', '', '## Quota-blocked case IDs (not passed)', '']
    for group, value in groups.items():
        lines.append('- ' + group + ': ' + (', '.join(f'{item["id"]} repetition {item["repetition"]}' for item in value['blocked_cases']) or 'none') + '.')
    lines += ['', 'F2 repetition 2 began but was interrupted during its daily-quota wait; it did not complete and is counted as quota-blocked, never passed or as a behavioral failure. The other listed repetitions were not run. S1/S2/S3/S5 completed through production code guards with zero provider requests; this is not a claim of live-model security coverage. S4 remains blocked in all three repetitions.']
    lines += ['', f'Grounded-answer source/citation proxy: {question_rate:.1%}; valid model-proposed tool calls: {valid}/{calls} ({tool_rate:.1%}). Expected source/evidence and citation assertions are unchanged; all schema-invalid proposals count as failures even when CrewAI rejects them.', '', f'Prior retrieval top-5: {gate1["hits"]}/{gate1["total"]}; Gate 1 extraction: zero wrong numbers accepted without a flag. Phase 4 does not rerun the VLM extraction set.', '', '## Preserved case failures', '']
    lines += [f'- {row["id"]} repetition {row["repetition"]}: {", ".join(row["errors"])}.' for row in failures] or ['None.']
    lines += ['', 'E01 failed before the optional-search-filter instruction was clarified. E07 exposed a password-reset punctuation bug; E17 exposed public loan-document wording misclassified as personal; E19 exposed percent-word versus percent-symbol validation. These were fixed with FakeLLM/code regressions, but their original cached failures remain failures and were never regenerated. E12 escalated a lost-card question without the expected policy citation.', '', '## Runtime and UI verification', '']
    lines += ['- ' + value for value in browser['results']]
    lines += ['- Production health verified Atlas ping, normal indexes and the single vector index READY before calls were stopped. A tiny text upload reached Ready; duplicate hash reused; original bytes matched; deletion returned 404.', '- Browser screenshots were inspected at 360px and 1280px using the existing Playwright package and installed Microsoft Edge. No browser/tooling was downloaded.', '- Browser interactions use a real HTTP fixture with FakeLLM. A fixture-only endpoint marks one text file Ready; the earlier live upload check exercised real background parsing/embedding separately.', '- The completed model scenarios used HTTP TestClient, native CrewAI and actual Atlas hybrid retrieval, with isolated synthetic account/request records. Evaluation requests were never submitted to live banking collections.', '- Terminal and rotating JSON output were read for UI/API/session/auth/prepass/agent/tool/flow/LLM/RAG/verification/upload/parse/ingestion/embedding events. Flow transitions and tool result codes were added after silent gaps were found.', '', '## Quota and limitations', '', f'Models used: {report["models_used"]}. Average logical tokens per completed chat turn: {report["average_tokens_per_chat_turn"]:.0f}; total {tokens}. Logical totals include successful cached responses; partial interrupted turns are excluded.', f'Last captured quota snapshot (not a final ledger; no fresh external query after stop): {quotas}. Phase 3 baseline: {gate2["quota_requests"]}.', '- Primary Qwen and then configured Groq GPT-OSS-20B fallback reached daily token limits. Configured model IDs were not changed. The complete primary-only Phase 4 run and the 14 listed repetitions remain unverified.', '- All live provider calls and retries stopped for today. No automatic resumption is scheduled. Only FakeLLM/code-only tests, builds, browser fixtures and local report generation continue.', '- Native provider calls retain the configured 25-second timeout; earlier real requests were paced to 8,000 TPM. Both passed and failed completed cases are input-hash cached and never regenerated.', '- Groundedness here is a conservative strict-case proxy including expected-source/evidence/citation and tool assertions plus runtime number validation, not an independent human semantic audit. Some otherwise source-backed answers also emitted irrelevant no-document notices.', '- No new live image-vision extraction set or larger multi-tool live test was run in Phase 4. Touch devices, screen readers and other browser engines remain for manual review. Follow docs/DEMO.md after starting the application when provider calls are permitted again.', '', 'Changed files: see eval/phase4_changed_files.md. No staging, commits, pushes or other Git mutations were performed.']
    (ROOT / 'eval/last_report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    status = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=ROOT, capture_output=True, text=True, check=True)
    changed = sorted({line[3:].replace('\\', '/') for line in status.stdout.splitlines()} | {'eval/phase4_changed_files.md'})
    (ROOT / 'eval/phase4_changed_files.md').write_text('# Phase 4 changed files\n\n' + '\n'.join('- `' + name + '`' for name in changed) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('release_passed', 'complete', 'groups', 'valid_tool_rate', 'failures')}, indent=2))


if __name__ == '__main__':
    main()
