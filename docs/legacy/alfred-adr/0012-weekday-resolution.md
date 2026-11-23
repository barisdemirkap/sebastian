# 0012. Weekday names and today's weekday

Status: accepted (owner decision, 2026-11-19).

## Context
On Thursday 19 Nov 05:01 the owner wrote "persembe öğleden sonra 4e" meaning today. The bot proposed Perşembe 26 Kasım 16:00 (next week) and the owner confirmed without noticing.

## Decisions
1. A bare weekday resolves to its next occurrence; `bu <gün>` is the next one, today included; `haftaya <gün>` is that day of next week; yarın, öbür gün unchanged.
2. When the owner names TODAY's weekday bare (`timeparse.names_today_weekday`: no bu, haftaya, bugün or explicit date) and the date is today or today+7, the assistant asks first: "Bugün Perşembe; bugün (19 Kasım) mi, haftaya (26 Kasım) mi?" with the choices `1) bugün 2) haftaya` (a word or the number answers; `evet` gets the question back). Exception: a clock time already passed today means next week without asking. The choice is stored in the clarification state (`daypick`), not in a pending action.
3. The same ambiguity for edit and delete targets looks at both days; several matches use the normal numbered pick.
4. Previews always show weekday and full date (`Perşembe 26 Kasım 2026`).
5. Parser: a dative hour ("4e") is a time and a day part ("öğleden sonra") belongs to it.

## Consequences
A wrong week needs two mistakes instead of one. Buttons for the choice would need a second keyboard kind; numbered choices were enough.
