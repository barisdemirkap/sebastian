# 0020. Agent evaluation: every incident fixture replays against agent mode

Status: accepted (owner decision, 2026-11-19).

## Context
ADR 0008 keeps a bot-agnostic conformance suite with incident fixtures. Agent mode needs the same safety net, deterministic in CI, and a way to measure the real model.

## Decisions
1. **Scripted fake model in CI** (`tests/conformance/agent_replay.py`, `test_agent_replay.py`): each fixture is first run against the dialogue manager and the state change of every handled message (new or edited draft, executed draft, list and task changes) is turned into the tool calls a good model would make (a directive per handled message). Replaying them through the real agent stack (grants, tools, composition, the Bot loop, buttons) must give the same state expectations and keep invariants I1-I10. Manager wording expectations (`contains`, `not_contains`, `replies_max`) are not asserted in agent mode.
2. **Hostile model**: the same replay with a model that tries to commit the open draft on every turn and claims it saved it (`Greedy`): only a code-minted grant lets a write through, and the false claim is replaced.
3. **Unit-level CI**: `test_agent_tools.py` (tools, versions, grants), `test_agent_mode.py` (gate conditions, composition, context, session rotation, fallback, breaker, cap, init-tools check, rollback and mid-draft flips in both directions), `test_agent_mcp.py` (the MCP server over a real socket, the exact lock-down flags, the stream parser).
4. **Manual real-model eval** `scripts/agent_eval.py [--fake | --real] [--k 3] [--only name]`: the same fixtures against `claude -p` with a temp DB and a fake calendar. It reports the wrong-write rate (must be 0), the repeated-question rate, turns-to-completion and pass^k (all k runs meet every state expectation). `--fake` replays the scripted model offline. A real run costs many model calls and is the owner's decision; `scripts/agent_smoke.py` (3 calls) is the cheap check.
5. **Bugs found by the replay** and fixed: the way-out menu appended after a preview killed the grant (the menu now goes before the preview in agent mode); the test adapter did not log the CLI import preview like `alfred import-plan --send` does.

## Consequences
- A new incident fixture is automatically replayed in both modes; no extra file is needed.

## Real-model results (2026-11-20, k=3, 63 scenarios; the three outage fixtures are manager-only, `"agent": false`)
- Before (prompt of ADR 0019): wrong-write rate 0, pass^3 46/65, repeated-question 22.4%.
- After (prompt with domains, every clause, resolve existing, completion closes, duration guess, queue note, fuzzy and undated times, numbered questions; new tool `task_set_due`): wrong-write 0, pass^3 55/63 before the last two prompt edits, 4 more scenarios fixed after (about 59/63), repeated-question 24%.
- Remaining misses are mostly fixtures that script the manager's exact numbered slot flow, or the two readings of 'hazir olalim'.
