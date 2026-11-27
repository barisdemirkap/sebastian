# 0008. English input: the same parsers, additive and accepted in every language

Status: accepted (owner-delegated follow-up to ADR 0006, 2026-11-27). Note: "ADR 0008" in a few test docstrings (`tests/conformance`) refers to the numbering of the private assistant these tests came from, not to this document.

## Context
ADR 0006 made replies selectable but left understanding Turkish-first: an English speaker got English replies and could not say "add dentist tomorrow at 3pm" without agent mode. The ADR named the missing piece, an English date and time parser, and deferred it. This ADR builds it.

## Decision
- One new module, `sebastian/english.py`, holds the English readers. It follows the rules of the Turkish ones: pure, deterministic, folded text (same length, so spans map back to the owner's words), and nothing is invented (no match means `None` and the assistant asks).
- Pack-driven where it helps: day and month names and the shopping-list names are read from the English language pack (`Pack.list_aliases`, `days`, `months`). The sentence grammar is English code, because grammar is not a word list. A future language adds its own reader module and registers it at the same two call sites; the pack carries its vocabulary.
- Additive, accepted in every language (like the English confirm words): a reply language does not switch it on. Turkish stays the primary reader and is consulted first.
  - `timeparse.find_date`, `find_duration` and `find_reminder` call the English reader only when the Turkish reader found nothing. `find_time` reads an explicit am/pm or "noon" in English first (the Turkish reader would take "10:30 pm" as 10:30), otherwise Turkish first. `names_today_weekday`, `mentions_date` and `mentions_time` (the guards against an invented model value) also know English words. Everything that already calls these (follow-up answers, list guards, pending-preview edits) reads English with no further change.
  - `rules.parse` asks `english.parse` first: it accepts only unmistakably English openings (add / schedule / book / create / put / set up / remind me ..., calendar questions, a greeting, a birthday question). Turkish text never matches, and a test pins that on the Turkish phrasebook.
  - `lists.parse` falls back to `english.parse_list` after every Turkish pattern failed.
  - The assistant recognises plain `help`, rewrites a relative start ("in 2 hours", "in half an hour") to "today at HH:MM" before any parser runs (rounded up to 5 minutes, so the preview shows the exact time), and understands English answers to its own questions ("tomorrow", "3pm", "evening", "all day", "another day").
- Preview and confirm are unchanged. English input only produces the same intent dictionaries the Turkish rules produce; every write still goes through the preview and the confirmation gate.
- Reading choices, made on purpose and visible in the preview:
  - "next Friday" is that day of next calendar week (like the Turkish "haftaya cuma"); "this Friday" is the coming one, today included; a bare weekday is the next one, and a bare weekday that is today is asked about (today or next week).
  - "next week" in a question lists the upcoming 14 days; "this week" lists the 7-day week.
  - A bare hour with "at" and no am/pm ("at 3") keeps the existing Turkish-style question "03:00 or 15:00?"; "at 7 in the evening", "3pm" and "at noon" are read without asking. A dated or timed wish next to a list wording is a calendar matter, as in Turkish.
  - A day-first or month-first date is read when a month name is spelled ("5 March", "March 5th"). Numeric "03/05" is not read: day first or month first cannot be told apart.

## What English still does not cover (honest limits)
- Changing, moving or deleting an existing event in English words ("move the dentist to Friday", "cancel Friday's meeting"). Use agent mode, or the Turkish wording.
- Task statements beyond `add task X`, `task: X`, `my tasks`: "I finished the report", "push the report to Monday", due dates in English.
- The clause interpreter (several statements in one message) and the purchase classifier ("we need milk", "milk is running low") are Turkish only. English list adds need "add X to the list" / "put X on the list" / "we are out of X"; ticking off needs "bought X" / "I got X".
- Recurrence beyond daily, weekly and "every <weekday>"; "midnight", "half past three", "quarter to", "the first Monday of next month", "in two weeks on Tuesday", numeric `03/05` dates.
- Model prompts (`nl.py`, `assist_prompt.md`, the agent prompt) remain Turkish-first; the English agent note makes replies English. Voice transcripts are not tuned for English.
- Slash commands keep the Turkish canonical names (English aliases exist for the common ones).

## Consequences
- A new English speaker can run the offline flow end to end: add events, ask what is on, keep a shopping list, and get English replies.
- Turkish behaviour is bit-for-bit unchanged: the Turkish suite (3966 tests before this ADR) passes untouched, new tests pin the English readers and that Turkish text is not claimed by them.
- The English reader is a second grammar to maintain; its boundary is `english.py` and its two call sites, and the limits above are the backlog.
