# 0003. Alfred sends its own reminders, digests and birthday notices

Status: accepted (owner decision, 2026-11-18)

## Decision
1. Reminders are sent by Alfred over Telegram, not by Google. Events Alfred creates carry `reminders.useDefault=false` and the override in a private extended property (`alfredReminder`, minutes). Timed events: default 30 minutes before (`reminders.lead_minutes`), per-event override from natural language ("bir saat önce hatırlat"). All-day events: 09:00 on the day (`reminders.allday_at`).
2. A reminder fires once when its moment has passed and the event has not started (all-day: the day is not over), so a restart or a late tick still delivers it. The sent-set is stored in the scheduler state file (`state.json`), which survives restarts.
3. Digests: 08:30 morning, 20:00 evening (unchanged), plus 22:00 "tomorrow morning plan" (tomorrow's all-day items and everything before 12:00). All text is Turkish from `i18n.py`; the older mail sections stay as they were.
4. Birthdays and anniversaries come from Google's Birthdays calendar (`addressbook#contacts@group.v.calendar.google.com`, read-only, `calendar.readonly` is enough) and from owner-added all-day events tagged `alfredTag=birthday` or titled "doğum günü". They are deduplicated by person and date. Morning digest: today and 3 days ahead. Evening digest: tomorrow. 22:00 plan: tomorrow's. They are shown in their own section, not as normal items, and get no separate 09:00 reminder.
5. A failing Birthdays calendar never hides normal events.

## Consequences
The Mac must be awake near reminder times (see `docs/deploy.md`); the heartbeat and watchdog (ADR 0005) restart a dead bot. The fetch window grows to 5 days to cover 3-day birthday notices and overrides.
