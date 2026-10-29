# Reversible cleanup record — local-llm

No Git mutations, tests, live model calls, database writes or data-file moves were performed.
All original files listed in manifest.json are recoverable at the same relative archive path.
65 original files were moved: 8 helper scripts, 45 files in 4 cache directories, 12 documents.
The document originals include three files replaced at their original paths, not simply removed.

## Step 1 evidence (recorded before moving)

Search scope: repository text, excluding .venv, node_modules, .git, data and generated/binary
output. Literal filenames were searched; Python dotted module references were also inspected.
The final pass includes frontend/dist text and omits only the requested excluded/protected paths.

### MOVE

| Item | Search and findings |
|---|---|
| scripts/run_gate1.py | Filename had no matches; scripts.run_gate1 is imported only by tests/test_gate1_live.py:12. Test-only cached retrieval/extraction runner. |
| scripts/report_gate2.py | Literal filename: eval/phase3_changed_files.md:41 only. Reads/writes evaluation output. |
| scripts/report_gate3.py | Literal filename: eval/phase4_changed_files.md:40 only. Reads/writes evaluation output. |
| scripts/check_http.py | Literal filename: eval/phase4_changed_files.md:37 only. HTTP smoke driver, not an app entry point. |
| scripts/check_ui.cjs | Literal filename: eval/phase4_changed_files.md:38 only. Playwright/Edge check driver. |
| scripts/check_queue.cjs | Literal filename: eval/requested_changes_report.md:158 only. Offline browser fixture driver. |
| scripts/check_local_llm.py | Literal filename: original local-model document:14,56 only. Local probe driver importing MemoryStore. |
| scripts/ui_fixture.py | Literal filename: eval/phase4_changed_files.md:43; dotted module only tests/test_requested_changes.py:90. FakeLLM browser fixture. |
| .pytest_cache, .ruff_cache, scripts/__pycache__, tests/__pycache__ | Searched all three cache names: only .gitignore:3,7,8; no app/config/prompts/kept-script/README/CLAUDE dependency. Regenerable caches outside protected paths. |

Retained tests that imported the archived helpers require restoring those helpers before running.
No test references were rewritten and no archived test/helper was executed.

### MERGE

| Original | Replacement and coverage |
|---|---|
| README.md | README.md: setup/run/inspection/demo; retired instructions replaced by current operational guidance. |
| CLAUDE.md | CLAUDE.md: all hard rules/style/Git constraints preserved; branch/doc list current; phase/gate sections removed as requested. |
| docs/01-architecture.md | ARCHITECTURE.md: components, online/offline flow, models, quota, concurrency and failures; stale plans corrected against current code. |
| docs/02-data-model.md | DB_MAP.md: 15 collections, fields, ownership, retention, indexes and storage considerations. |
| docs/03-tools.md | ARCHITECTURE.md: envelopes, tool list/arguments, visibility, limits, citations and code-only operations. |
| docs/04-auth-and-flows.md | ARCHITECTURE.md: auth transitions/locks/replay, flows/slots/summary/confirmation/idempotency and edge conditions. |
| docs/05-rag-and-parsing.md | ARCHITECTURE.md: parsers, image cascade, validation, chunking, embedding, retrieval, upload caps and sample documents. |
| docs/06-frontend-and-logging.md | ARCHITECTURE.md: UI/API/cards/queue/expiry, trace areas/spans, token metering and logging. Current queue behavior supersedes old disable-while-thinking wording. |
| docs/DB_MAP.md | DB_MAP.md: expanded map retaining the existing collections/provenance/inspection/migration guidance. |
| docs/DEMO.md | README.md six-step demo: exact messages/files/expected outcomes retained; current local limitations and OTP transcript behavior corrected explicitly. |
| docs/LOCAL_LLM_CHANGES.md | LOCAL_LLM.md: current behavior/settings/switch plus complete historical change/probe/trace record. |
| docs/DECISIONS.md | LOCAL_LLM.md: complete original decision record appended, clearly historical where settings changed. |
| BUILD_PROMPTS.md; docs/07-tests.md | ASK: left in place, because their complete phase/gate content is not covered by the operational replacements. |

Original docs had CLAUDE/README references. New README/CLAUDE were written first, old copies
archived, replacements installed, then references re-searched before moving superseded docs.
At that point no app/config/prompts/kept-script/README/CLAUDE reference blocked the doc moves.
Every replacement was written before its original was archived. ARCHITECTURE is 273 lines.

### ASK / kept

| Item | Evidence / reason |
|---|---|
| tests/ | Initial README:43,66,84,86 and CLAUDE:44,50 references; safety rule blocks whole-directory move. Only its unreferenced bytecode cache moved. |
| eval/ | Initial README:52,92; CLAUDE:44; retained evaluation runners reference its data. |
| scripts/run_gate2.py, run_gate3.py, retry_gate1_images.py | Initial README:101–102,45,110 respectively; kept despite being test-only. |
| requirements-dev.txt | Only self-reference, but includes -r requirements.txt, pytest dependencies and ruff: not exclusively test packages. |
| scripts/generate_seed.py | Filename search: no references. User explicitly requested ASK, so retained. |
| scripts/migrate_storage.py | Referenced by old data-model/map and tests; optional destructive legacy migration capability, not safe to assume disposable. |
| uv.lock | A separate uv lock; requirements.txt is authoritative here, but user explicitly requested ASK. |
| src/ | Packaging points to app, but unused scaffold removal was not explicitly authorized; retained. |
| BUILD_PROMPTS.md, docs/07-tests.md | Full historical phase/test contracts not merged; never archive incompletely covered documents. |
| All non-protected data/ contents | Runtime checkpoints, embedding/vision caches, artifacts, fixtures and logs may be read by code. All were left untouched, including data/test_cache. |
| scripts/dev_backend.py | scripts/dev.mjs:6 spawns it: required runtime dependency, retained. |
| frontend/dist | Served by app.main when built; retained. |
| app/**/__pycache__ | Inside protected app/; left untouched. |

The protected .env, .venv, .git, node_modules, runtime code/config/prompts/UI, bank documents,
uploads, tools, log file, dependency manifests/locks, seed JSON and required scripts were not edited.
The explicitly requested app import may load runtime settings but did not print secrets or start workers.

## Database dry run

Executed only `.venv\Scripts\python.exe scripts\clean_test_data.py`, without --yes.

| Collection/predicate | Count |
|---|---:|
| sessions / all | 1 |
| pending_actions / all | 0 |
| audit_log / all | 113 |
| requests / all | 11 |
| documents / namespace=user | 1 |
| sections / namespace=user | 1 |
| chunks / namespace=user | 1 |
| rate_tables / namespace=user | 0 |
| rate_rows / namespace=user | 0 |
| extractions / namespace=user | 1 |
| Total | 129 |

No collection drop, delete, update, index mutation, file deletion or file cleanup was invoked.
The new optional --yes path uses only fixed collection/predicate pairs; it never targets
users/accounts/cards/transactions/quota_usage or namespace=bank rows, and never removes files.

## Checks and references

`.venv\Scripts\python.exe -c "import app.main"` completed successfully. Bytecode writing was
disabled for the checks to avoid recreating moved caches or changing protected app caches.
No test runner or live model endpoint was invoked. Git remains local-llm with no staging/commit/push.
README links now point to consolidated docs/demo; CLAUDE doc list/style links were updated.
No remaining runtime/README/CLAUDE/script references require fixing. Retained historical build,
eval and test records may still name archived originals; they are deliberately not rewritten.
A basename search can also match the live root README against archived cache README files;
that is not a broken reference. New LOCAL_LLM's historical helper path points into this archive.
