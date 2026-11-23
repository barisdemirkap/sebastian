# 0023. Model assist for statements (shadow first)

Status: built as a shadow experiment, then DROPPED from the shipped configuration (default `off`) because the kill criterion fired (see "Outcome"). Code kept behind `ALFRED_NL_ASSIST=shadow|on`. Amends ADR 0022 (task writes).

## Context
ADR 0022's keyword rules reach 100% on the fixtures they were trained on, but every round of fresh, free-form phrasing in the owner's style
missed again (blind first-pass 80, 74, 80, 74, 84, 82, 90, 94, 90, 100, 82, 74 percent over rounds 4 to 15). A board review asked whether
a model fixes the cause or only the symptom, and required the plan to be revised: close the safety gap first, measure what the misses are,
and only then add a model, rules first, in shadow.

## Decisions
1. **Task-write safety (done first).** `statements.py` (and the older task flows) never remove or bulk-change a task silently. Removal
   ('sil/kaldir/iptal' of a task), bulk done, bulk defer ('hepsi', 'digerleri', 'geri kalan') and a name that fits more than one task become ONE
   pending preview (`task_write`) that lists the resolved TITLES and waits for 'evet' (buttons too). One clearly resolved task may still be
   closed or moved at once, and the reply names it. Completion still never proposes deleting. The stored ids are applied on 'evet'; a task that was
   closed meanwhile is skipped.
2. **Failure buckets** (589 blind messages of rounds 4 to 15, run against the code as it was BEFORE each round's fixes; 74 first-pass misses,
   12.6%): fell through to an older flow 33 (45%); wrong label 18 (24%); wrong item 19 (26%); date/time 4 (5%). Misses are lexical coverage
   (done participles, typos, chatter next to a statement) and reference matching, NOT date/time: the 4 date/time misses ('bugun olmaz cumaya',
   'bu yarina degil pazartesi', 'pazar yeri' read as Sunday) were fixed deterministically in the rules and stay there. A model is not the fix
   for dates; code resolves every day and time from the clause's own words.
3. **Model assist, rules first** (`assist.py`, `assist_prompt.md`, rubric in `docs/nl-assist-rubric.md`). Called ONLY when the rules cannot read
   a message with confidence (nothing to act on, an unreadable clause, or an acting clause whose reference resolved to nothing); never for
   trivial messages (under 3 words, questions, commands, explicit 'gorev ekle' / list wording, event creation, anything while a preview or
   question is open). The model returns strict-schema JSON (`claude -p --json-schema`): clauses of {span, kind, ids, scope}. Code rejects the
   WHOLE answer when an id is not in the candidate list the code supplied (open tasks `T<id>`, upcoming events `E<n>`), a span is not a substring
   of the message, a kind is unknown, an extra field appears, or a defer has no day. Days and clock times come from `clauses._day/_time` over the
   clause's own words; fuzzy name matching is not used for a model clause (the model picked a candidate).
   - **Single source of truth: the model's kind is authoritative for a clause it handled.** The model only sees messages the rules were not
     confident about, so the two do not compete; the regex overrides on raw text in `statements.py` ('gecmis', stay-verbs, 'iptal') are skipped for
     model clauses. A model clause of kind read/add/other (or an unsure answer) makes the rules' answer stand.
   - Model ID pinned: `claude-haiku-5-5` (`ALFRED_NL_ASSIST_MODEL`). The CLI has no temperature flag, so temperature 0 cannot be set on this path
     (recorded as `temperature: cli-default (no flag)` in the audit row); determinism comes from the strict schema plus code validation. Timeout 10 s, any
     failure (timeout, usage, schema, rejected id) falls back to the rules. Raw output, model, latency and cost are logged (`audit_log` action
     `nl_assist`). Needs ALFRED_NL=claude plus the owner's consent; counts against the daily NL cap.
   - **Mode `ALFRED_NL_ASSIST` = off | shadow | on, default `shadow` when written, `off` after the Outcome below**: the answer is logged with its comparison to the rules (`agree_kinds`,
     `agree_ids`) and never acted on. `on` is the promotion. Even promoted, every task write that comes from a model reading shows a preview and
     waits for 'evet'.
4. **Spike** (`scripts/assist_spike.py`, REAL calls through this machine's `claude -p`, 40 calls, $0.0176; cassette in
   `tests/cassettes/assist_spike.json`). Same 589 messages, old rules, model promoted on low confidence:
   - first-pass 87.4% (rules only) to 92.2% (rules + model): 28 of the 74 misses fixed, 0 regressions, 40 calls (6.8% of messages);
   - kind accuracy 35/35 and reference accuracy 34/35 on the calls that had a gold (kind and reference scored separately); wrong-item rate 3 of 48
     acted items (6%, mostly 'others' expansion counted against a narrow gold);
   - latency p50 3.7 s, p95 5.3 s per call (only on the 7% of messages that reach the model); cost $0.00044 per call, $0.00003 per message.
   - By bucket: fell through 12/33 fixed, wrong label 5/18, wrong item 5/19, date/time 0/4. The model helps lexical coverage; it does nothing for
     the rules' confidently-wrong readings (the gate never fires on them) and nothing for dates.
   Caveats: in-sample for the rules (they were trained on these fixtures afterwards), n is small, the model is not deterministic, and the prompt's
   examples were rewritten after a first run so none is a fixture message.
5. **Break-even.** A word-list round costs about one working session (50 new messages, analysis, 8 to 52 changed lines per round, 10 rounds in one
   evening) and its gain does not carry over: the next fresh phrasing missed 18% to 26% again. The model path was built once (about one session) and
   costs about $0.0006 per day at 20 messages a day. It breaks even after roughly 1.5 word-list rounds IF it holds up on fresh text. That is what shadow
   mode tests.
6. **Kill criterion (stated before the fresh rounds).** Over one fresh blind round of at least 40 messages scored with the spike script: drop the
   assist (delete `assist.py`, keep the safety gate) unless it fixes at least 3 of the rules' misses with 0 regressions and a wrong-item rate of
   at most 5% of acted items. Promotion to `on` additionally needs two weeks of shadow rows in which the model agrees with a confident owner follow-up.
   A 50-message round cannot resolve less than about 4 points, so no smaller margin is claimed.

## Consequences
Shipping this changes nothing the owner sees except the task-write previews (decision 1). Shadow adds one model call, 4 to 8 s of latency, to about 7% of
messages and logs the comparison; set `ALFRED_NL_ASSIST=off` to remove it. The agent, dialogue and one-shot intent paths are untouched.

## Outcome (fresh blind rounds, run after the criterion was written)
- Round 16 (40 new messages): rules-only 39/40 (97.5%); with the model promoted 39/40; the one miss ('bugun yetisemedi' read as a move to today) was a
  rules reading the gate never doubts, so the model was not consulted: 0 fixes, 0 calls.
- Round 17 (40 new messages): rules-only 37/40 (92.5%); with the model promoted 39/40: 2 fixes ('iptal edildi', 'gerek kalmadi'), 0 regressions, 6 calls, $0.002.
  The third miss ('disci' for 'Dis hekimine git') was a reference the model was never asked about.
- Kill criterion (at least 3 fixes with 0 regressions in one fresh round): NOT met in either round (0 and 2). Applied as written: the assist is off by
  default. The earlier 589-message result (28 of 74 fixed) is real but in a setting where the rules were still being fitted to those same messages; on fresh text
  the rules miss about 5% and the model fixed 2 of the 4 misses it could see. The honest reading is that the model is a plausible fix for the 'fell through'
  bucket and an unproven one for the rest; the owner can set `ALFRED_NL_ASSIST=shadow` to collect real-log evidence for the two-week promotion check.
- The rules fixes made from these rounds are deterministic: third-person 'yetisemedi' is not done; 'X iptal edildi' is a completion; 'X'e gerek kalmadi' previews
  a removal; a dative weekday beats a bare 'yarin' ('yarin gidemem cumartesiye birak'); 'disci' names the dentist.
- A bug found on the way: a model answer that could not be applied (an unsure 'other' clause) made the whole message fall through instead of leaving the rules'
  reading standing. Fixed, with a test.

## Blind rounds of the shipped configuration (rules + task-write safety, assist off)
First-pass pass rates on fresh, pre-written, real-log-style rounds of 40 messages: r16 97.5%, r17 92.5%, r18 95.0%, r19 90.0%, r20 100%, r21 97.5%.
Two consecutive rounds at or above 95% were reached at r20 and r21. Honest limits: these rounds are written by the same author as the rules and share a
shape (one statement per message, three task worlds per round, day words from a short list), so they measure phrasing drift, not the owner's real
distribution; r21's single miss ('camlar bu hafta olmaz haftaya') is an expectation question ('haftaya' is +7 days in this codebase, the fixture said Monday)
and was corrected to the codebase convention. The rounds also show the pattern the board feared: each round still finds 1 to 4 new lexical gaps
(a done verb, a bare weekday), so the rules are 'trained' for this distribution but not closed.
