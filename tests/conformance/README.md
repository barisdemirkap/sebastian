# Conformance suite

Bot-agnostic checks that run scripted conversations and assert invariants I1-I10 after every turn (see the docstring of `invariants.py` and ADR 0008). Fast and deterministic. Ported from the Acme suite; Sebastian imports nothing from it.

Run: `.venv/bin/python -m pytest tests/conformance`

## Add an incident in 5 minutes
1. Copy an `incidents/*.json` file to `incidents/<yyyy-mm>-<slug>.json`.
2. Fictionalise: no real names, events or chat ids.
3. Write the steps that reproduce the bug: `{"say": "...", "chat": 1, "kind": "confirm|cancel|edit|neutral|free"}` plus expectations (`contains`, `preview`, `preview_count`, `preview_has`, `saved_total`, `saved_has`, `replies_max`, `facts`). `chat` 1 is the owner; any other number is a stranger. `say` may be a list (a burst); `{"redeliver": true}` repeats the last update; `{"network": "down"|"up"}` breaks or restores sending. Setup keys: `provider` (rules | claude_like | fail), `seed` (calendar events), `import` (open an N-event import preview).
4. Run the suite; it must FAIL. Then fix the bug until it is green. (Rule: incident fixture first, then the fix.)

## Port to another bot
Copy `invariants.py`, `phrasebook_tr.py` and `incidents/` next to a new adapter. The adapter implements the contract in the `invariants.py` docstring and a `make_adapter(setup)` factory, wired to the bot's real update path. Only the adapter knows the bot.

## Agent mode (ADR 0020)
`agent_replay.py` derives, for every incident fixture, the tool calls a good model would make (from what the dialogue manager does) and replays the conversation against agent mode with a scripted fake model; `test_agent_replay.py` asserts the state expectations and I1-I10, also with a hostile model that tries to commit on every turn. A new incident is replayed automatically. The real model: `scripts/agent_eval.py --real --k 3`.
