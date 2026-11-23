# 0002. Preview-gated calendar writes (supersedes the read-only clause of ADR 0001)

Status: accepted (owner decision, 2026-11-18)

## Context
ADR 0001 made Alfred read-only. The owner now wants Alfred to manage his Google Calendar: add, edit, move and delete events, set reminders, birthdays, from free-form Turkish. That needs a write scope.

## Decision
1. Google accounts may request `calendar.events` (narrowest write scope) in addition to `calendar.readonly`. `oauth.check_scopes` still refuses `calendar` (full), `Calendars.ReadWrite`, mail write/send scopes and everything else. Outlook stays read-only.
2. Writes are live only with BOTH `ALFRED_ALLOW_LIVE=1` and `ALFRED_ALLOW_WRITE=1`, enforced twice: in `GoogleCalendarProvider` (also requires the scope in the account config) and in `UrllibTransport` (any non-GET to the Calendar API without the flag raises).
3. A write is executed only from a stored pending action after the owner types the explicit confirmation (ADR 0003). Delete needs the stronger wording `evet sil`. Nothing else in the code base calls `create_event`, `update_event`, `delete_event`.
4. Alfred sets `reminders.useDefault = false` on events it creates and sends its own Telegram reminder (stored as private extended property `alfredReminder`), so there is no double notification.
5. Mail stays read-only and metadata-only. Sending to third parties still needs a new ADR.

## Consequences
A mistaken or injected instruction can at worst produce a wrong preview that the owner declines. Revoking access in the Google account (see `docs/GOOGLE-SETUP.md`) removes all write ability. ADR 0001 clause 2 is amended for Google Calendar events only.
