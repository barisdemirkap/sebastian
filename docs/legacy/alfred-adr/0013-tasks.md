# 0013. Tasks (owner to-dos)

Status: accepted (owner decision, 2026-11-19).

## Context
"camasir bitti" (a finished to-do) got "do you want me to add a reminder or event?". A to-do is not a calendar event.

## Decisions
1. **Task**: a line in Alfred's own list (`tasks` table: title, status open | done | removed). It is not a calendar write and needs no preview.
2. **Wordings** (`tasks.parse`, deterministic, before the model): add `görev ekle X`, `X yapmam lazım`; list `görevlerim`, `/gorevler`; done `X bitti|bitirdim|yaptım|tamamlandı`, and `X tamam` only when a task or event matches; remove `X görevini kaldır` (and plain `X'i kaldır` when no event matches). A dated or timed wish stays a calendar matter.
3. **Finishing** marks the task done. If a calendar event of today or tomorrow matches, a normal pending delete preview is OFFERED (`evet sil` / Sil button); the calendar is never changed without it. With no task and no event the bot says so and shows `görev ekle`.
4. **Digests**: open tasks are listed at the end of the morning and evening digests (`Scheduler.tasks`).

## Consequences
Plain "X'i kaldır" prefers an event over a task. No due dates or reminders for tasks yet.
