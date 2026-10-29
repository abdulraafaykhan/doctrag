# Gate 3 release report

**Release bar: NOT MET.** Evaluation complete: False. All live model, embedding and vision calls/retries stopped at the user's request; the background application was stopped too.

Offline full suite: 114 passed, 1 skipped, 0 failed. Final code-only API/privacy/masking check: 33 passed. Production build: passed (React 19 / Vite). Earlier live HTTP health/upload lifecycle: True.

| Group | Passed | Failed | Quota-blocked / not run | Planned |
|---|---:|---:|---:|---:|
| question | 19 | 11 | 0 | 30 |
| flow | 4 | 0 | 11 | 15 |
| attack | 12 | 0 | 3 | 15 |
| Total | 35 | 11 | 14 | 60 |

## Quota-blocked case IDs (not passed)

- question: none.
- flow: F2 repetition 2, F2 repetition 3, F3 repetition 1, F3 repetition 2, F3 repetition 3, F4 repetition 1, F4 repetition 2, F4 repetition 3, F5 repetition 1, F5 repetition 2, F5 repetition 3.
- attack: S4 repetition 1, S4 repetition 2, S4 repetition 3.

F2 repetition 2 began but was interrupted during its daily-quota wait; it did not complete and is counted as quota-blocked, never passed or as a behavioral failure. The other listed repetitions were not run. S1/S2/S3/S5 completed through production code guards with zero provider requests; this is not a claim of live-model security coverage. S4 remains blocked in all three repetitions.

Grounded-answer source/citation proxy: 63.3%; valid model-proposed tool calls: 39/41 (95.1%). Expected source/evidence and citation assertions are unchanged; all schema-invalid proposals count as failures even when CrewAI rejects them.

Prior retrieval top-5: 29/30; Gate 1 extraction: zero wrong numbers accepted without a flag. Phase 4 does not rerun the VLM extraction set.

## Preserved case failures

- E01 repetition 1: expected source/evidence missing, answer lacks expected citation, verification declined answer.
- E07 repetition 1: expected source/evidence missing, answer lacks expected citation.
- E12 repetition 1: expected source/evidence missing, answer lacks expected citation.
- E17 repetition 1: expected source/evidence missing, answer lacks expected citation.
- E19 repetition 1: answer lacks expected citation, verification declined answer.
- E21 repetition 1: expected source/evidence missing, answer lacks expected citation.
- E22 repetition 1: expected source/evidence missing, answer lacks expected citation.
- E23 repetition 1: expected source/evidence missing, answer lacks expected citation.
- E24 repetition 1: invalid tool proposal.
- E27 repetition 1: invalid tool proposal.
- E28 repetition 1: required tool missing.

E01 failed before the optional-search-filter instruction was clarified. E07 exposed a password-reset punctuation bug; E17 exposed public loan-document wording misclassified as personal; E19 exposed percent-word versus percent-symbol validation. These were fixed with FakeLLM/code regressions, but their original cached failures remain failures and were never regenerated. E12 escalated a lost-card question without the expected policy citation.

## Runtime and UI verification

- sent bubble visible before response; thinking and disabled send
- reload restores message and reply
- second tab receives 409 and displays the message
- pending attachment card and remove button delete early upload
- files-only send; sent attachment opens and survives reload
- OTP note, verified login chip, balance and option clicks as sent messages
- explicit confirmation submits one request and shows its reference
- GFM table and citation chip render; Sources includes page/version/date
- failed send stays visible; Retry reuses the bubble
- image thumbnail and in-page preview open/close; pending image removal
- no horizontal overflow; composer visible at 360px
- no horizontal overflow; composer visible at 1280px
- dark scheme follows system preference
- Production health verified Atlas ping, normal indexes and the single vector index READY before calls were stopped. A tiny text upload reached Ready; duplicate hash reused; original bytes matched; deletion returned 404.
- Browser screenshots were inspected at 360px and 1280px using the existing Playwright package and installed Microsoft Edge. No browser/tooling was downloaded.
- Browser interactions use a real HTTP fixture with FakeLLM. A fixture-only endpoint marks one text file Ready; the earlier live upload check exercised real background parsing/embedding separately.
- The completed model scenarios used HTTP TestClient, native CrewAI and actual Atlas hybrid retrieval, with isolated synthetic account/request records. Evaluation requests were never submitted to live banking collections.
- Terminal and rotating JSON output were read for UI/API/session/auth/prepass/agent/tool/flow/LLM/RAG/verification/upload/parse/ingestion/embedding events. Flow transitions and tool result codes were added after silent gaps were found.

## Quota and limitations

Models used: ['openai/gpt-oss-20b', 'qwen/qwen3.8-27b']. Average logical tokens per completed chat turn: 2851; total 173921. Logical totals include successful cached responses; partial interrupted turns are excluded.
Last captured quota snapshot (not a final ledger; no fresh external query after stop): [{'day': '2026-10-26', 'llm_requests': 243, 'embed_requests': 114, 'vlm_requests': 41}, {'day': '2026-10-27', 'llm_requests': 24, 'embed_requests': 6, 'vlm_requests': 0}]. Phase 3 baseline: {'embed_requests': 101, 'vlm_requests': 41, 'llm_requests': 191}.
- Primary Qwen and then configured Groq GPT-OSS-20B fallback reached daily token limits. Configured model IDs were not changed. The complete primary-only Phase 4 run and the 14 listed repetitions remain unverified.
- All live provider calls and retries stopped for today. No automatic resumption is scheduled. Only FakeLLM/code-only tests, builds, browser fixtures and local report generation continue.
- Native provider calls retain the configured 25-second timeout; earlier real requests were paced to 8,000 TPM. Both passed and failed completed cases are input-hash cached and never regenerated.
- Groundedness here is a conservative strict-case proxy including expected-source/evidence/citation and tool assertions plus runtime number validation, not an independent human semantic audit. Some otherwise source-backed answers also emitted irrelevant no-document notices.
- No new live image-vision extraction set or larger multi-tool live test was run in Phase 4. Touch devices, screen readers and other browser engines remain for manual review. Follow docs/DEMO.md after starting the application when provider calls are permitted again.

Changed files: see eval/phase4_changed_files.md. No staging, commits, pushes or other Git mutations were performed.
