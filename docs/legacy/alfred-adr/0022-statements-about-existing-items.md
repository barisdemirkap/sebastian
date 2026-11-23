# 0022. Statements about existing items (training loop)

Status: accepted (owner-approved training loop, 2026-11-19).

## Context
The owner's message log showed five recurring failures: a reference to an existing task or event created a new one, a completion statement offered to delete the event, a clause of a long message was dropped, 'bu yarina' closed tasks instead of moving them, and an unambiguous read ('yarinin programi nasil?') got 'type the number'.

## Decisions
1. **Principles** (enforced by `tests/conformance/training/` and the incident fixtures): (1) a reference to an existing event, task or list item is resolved, never created again; (2) a completion statement closes the item and never proposes deleting it; (3) every clause of a multi-clause message is processed; (4) 'bu yarina', 'yarina kalsin', 'X'e tasi' reschedule (task due day, event move preview), never close; (5) no stray 'type the number' question when the reference is unambiguous or follows an earlier answer.
2. `clauses.py` (pure) cuts a message into clauses and reads each as done, defer, partial, remind, delete, keep, plan, forgot, add, read or other. `statements.py` (a mixin of `Assistant`) resolves every subject against the open tasks and the next 8 days of the calendar and applies the statement. It runs before the older task flows and returns None when it does not claim a message (a single clause that resolves to nothing, an unreadable clause, a preview-edit while a preview is open, ready-time edits).
3. **Done**: a task is closed; a matching event of today (the latest that has started) is closed in Alfred's own `event_done` table and shown with a check mark; it is never deleted from the calendar. This supersedes the 'offer a delete preview' part of ADR 0013. An explicit 'sil/kaldir' still makes a delete preview that needs 'evet sil'.
4. **Defer**: a task gets a `due` day; a matching event gets an edit preview (date change). 'Digerleri' and 'hepsi' apply to the open tasks the message did not name; 'digerlerini tamamladim' closes them.
5. Previews that cannot be open together (move, delete, reminder, older-flow clauses) are queued in `action_queue`, announced with the clause-queued line, and shown one at a time after each answer (`_drain_actions`).
6. 'X unuttum' asks once 'evening or tomorrow?' (`defer_when`); the answer ('aksama', 'yarina', a weekday) moves the item, it is never searched as an event name.
7. A generic shared word ('toplanti', 'randevu') alone is not a mention of an existing event when the message names something else.

## Consequences
- Fixtures: `tests/conformance/training/*.json` (150 offline cases, run by `test_training.py`; expectations `tasks_open_has`, `tasks_done_has`, `tasks_due`, `tasks_open_count`, `tasks_done_not`, `tasks_removed_not_has`) plus the real-log incidents.
- Task reminders ('bunu bu aksam 10 gibi hatirlat', 'X cumartesi 11de hatirlat') resolve 'bunu' to the item talked about last (`last_item`), preview a calendar entry at the exact time (reminder 'tam saatinde') and keep the task open. A plan statement with a clock time ('cop aksam cikacak 18.30da') whose subject is an open task previews an entry titled like the task, which replaces the task on save.
- Name collisions are broken by exact-word and apostrophe-suffix bonuses (Ali'yi vs Ali'ye, su faturasi vs elektrik faturasi); a long sentence that shares one word with an event title no longer moves that event.
- Training status: 15 rounds, ~700 cases. Blind first-pass rates stayed in the 74-90% band for fresh phrasing and are not yet at the 95% bar; see the training report in the commit history.
- Not done: several new events in one message are only split by the rules when no model is configured; telegraphic 'X pazar Y yarin' (no verbs), slang passives ('sisti') and bulk 'mark every past event done' are not read.
