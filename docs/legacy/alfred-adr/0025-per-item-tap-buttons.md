# 0025. Per-item tap buttons

Status: accepted (owner-approved, 2026-11-23).

## Context
Closing one task or ticking one list item meant typing a sentence. The owner wants to tap.

## Decisions
1. The task list view and each list view carry one inline button per item (`t:<id>:<hash>` for tasks, `i:<id>:<hash>` for list items), capped at 20; the all-bought and clear row stays last under a list.
2. The hash covers the row id and its text; a tap is verified against the stored row (owner, status, unchanged text). Anything else answers the stale alert.
3. A task tap closes the task (one-way); a list item tap toggles open and done. Own store only: no calendar write, no preview needed.
4. Taps are logged as inbound messages and audited by name (`task_tap_done`, `list_item_tap`).

## Consequences
- Tests in `tests/test_lists.py`. The callback pattern in `bot.py` accepts `i` and `t`.
