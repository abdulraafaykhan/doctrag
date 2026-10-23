# Code rules

These rules apply to every task, small or large. They exist for three goals: **correct execution, fast execution, and the least code that works.**

## 1. Prime directive

Make the smallest correct change. Simple, short code that works beats clever, long code. If a short version and a long version behave the same, write the short one.

## 2. Scope discipline (do not change what you were not asked to change)

- Touch only the files and functions the request needs. No drive-by renames, reformatting, import reordering, comment rewrites or "while I'm here" cleanups in code the task does not involve.
- A simple request (a message wording, a setting, one validation, one bug) should change **one or two files**. If your diff for a simple request passes about 40 lines or three files, stop and tell me why before continuing.
- If you notice an unrelated problem, **list it in your report, do not fix it**. The one exception is stage 4 of `docs/PHASES.md`, the planned refactor.
- Never change a public behaviour, route, event name, option id or data field that the task did not mention. If you must, say so in the report.
- Do not rewrite a working module to match your taste. Do not add features, settings or abstractions nobody asked for.

## 3. Simplicity rules

- **Data over code.** Prefer a table, dict or dataclass that a small generic function reads, over many near-identical functions. Flows, tool specs, fees and messages are data.
- One function, one job. Aim for under about 30 lines. Early returns instead of deep nesting.
- Functions before classes. Add a class only for state that really belongs together.
- No abstraction until a second use exists. No base classes, plugins or factories "for later".
- No defensive code for situations that cannot happen. Validate at the edges (request, option id, user text, provider reply), then trust the types.
- Reuse what exists. Before writing a helper, search the code for one (`Grep`).
- New dependency only when the standard library or an existing dependency cannot do it in a few lines. Justify it in a comment and the report.
- Delete dead code, unused imports, unused settings and commented-out code you meet **inside the code you are editing**.

## 4. Correctness rules

- Reproduce every bug with a **failing test first**, then fix it. Every bug fix ships with a regression test.
- Validate on the server. Button option ids, typed answers, amounts and tenures are checked against the current step. Never trust the client.
- Writes are **idempotent** and state changes are **atomic**. A double click or a retried request must not create two service requests.
- Use an injectable clock for anything time-based (expiry, OTP, locks) so tests can move time.
- Money is integer minor units. Compute EMI and fees in code, never with the LLM.
- Never swallow an exception silently. Catch the narrowest type, log the cause, return a friendly message.
- A provider error (Groq, Gemini) logs its HTTP status and error message (never request content).
- Answers about personal data come from tool results. If data is missing, say so; never invent it.

## 5. Speed rules

- Zero LLM calls whenever code can decide: verification, flows, fixed replies, clear intents, account answers from a single data tool.
- One LLM call for a normal document answer. Never more than the budget in `docs/REDESIGN.md` section 4.
- Read the session once per turn. Do not repeat database reads for the same row in one turn.
- Run independent awaits together (`asyncio.gather`) only when they truly do not depend on each other.
- Log per-turn timing (see `docs/LOGGING.md`) so slowness is visible.

## 6. Comment standard

Comments are required. Keep them short and plain, in this form:

- **Module header:** a 1 to 3 line docstring saying what the module is for.
- **Major decision comment**, starting with `# Decision:`. Put one at every major choice: a feature added, a library added or called (say why it was chosen), a module introduced, a security or data-safety choice.
- **Minor action comment**, a plain `#` line, on small actions that help a reader learn the codebase: what a helper returns, what a state means, why a value is masked, why a check exists.
- Do not restate obvious syntax (`# increment i`). Do not leave stale comments. Do not write essays.

Example:

```python
# Decision: flows are data (a list of steps), so adding a service needs no new control flow.
CHEQUE_BOOK = FlowDef(name="cheque_book", steps=[...])

# Customer id comes from the verified session, never from the request.
customer_id = session.verified.customer_id
```

## 7. Test rules

- Small, fast, deterministic. Fake Mongo, fake LLM, fake clock. No network.
- Assert on **behaviour and option ids**, not on full sentences, so a wording tweak does not break tests.
- Never weaken or delete a test to make it pass. If a test is wrong, say why in the report.
- Run the narrow test file first while working (`uv run python -m pytest tests/test_x.py -q`). Run the full suite at the end of a stage.

## 8. Token strategy (do not waste tokens)

- **Search before you read.** Use `Grep` to find the function, then read only that range (offset and limit). Do not open whole large files or the whole repo.
- Read each file once. Do not re-read a file you just wrote or edited.
- Plan in at most ten lines, then act. Do not write long plans, essays or restate the docs.
- Edit hunks with `Edit`. Do not regenerate a whole file to change a few lines. Batch the edits to one file together.
- Do not print big outputs. Limit command output (head, `-q`, a path filter). Never paste long logs back.
- Do not re-run a passing command. Run the full test suite once per stage, not after every edit.
- Do not explore "just in case". If something is ambiguous, ask one short question instead of reading ten files to guess.
- Write each piece of code once and correctly. Think about the edge cases before typing, not through repeated rewrites.
- Reports are short (section 9). Never paste code back into the report.
- If you are running low on context, **stop at the end of a stage**, make sure the suite is green, and report what is done and what is next. Never leave half a stage.

## 9. Report format (end of every stage or task)

At most about 40 lines:

1. **Done:** one line per item.
2. **Files changed:** paths only.
3. **Dependencies and settings:** added or removed.
4. **Differences from the docs, and unrelated problems noticed:** one line each.
5. **Decisions I need from you:** questions only.
6. **Verify:** the exact PowerShell commands, and what to look for.

## 10. Refactor protocol (stage 4 only)

- **Behaviour must not change.** Tests are green before you start and after every step.
- Do not mix a refactor with a feature or bug fix.
- Targets: dead code, unused settings and fields left by earlier phases, duplicated helpers, functions over about 40 lines, inconsistent names, scattered fixed messages (one module), missing or stale comments under the standard in section 6.
- Work in small steps by area. Prove "dead" with a search for references before deleting.
- Report lines of code before and after, and anything you chose not to touch.

## 11. When to stop and ask

Stop and ask me, do not guess, when: a rule in `AGENTS.md` and the task conflict; a decision touches security or data safety; the docs and the code disagree and the choice changes behaviour; a task seems to need a new dependency; or you cannot reproduce a bug.