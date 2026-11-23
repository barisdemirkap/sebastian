# 0024. Tentative planning

Status: accepted (owner request, 2026-11-20, msg 907).

## Context
'bu gorevleri de planla' (msg 155) was answered with 'which tasks do you want me to plan? title, date, time': the reference to the open tasks was not resolved, and Alfred could only schedule one named event.

## Decisions
1. A request to plan ('planla', 'plana al', 'plan yap', 'yerlestir') that refers to tasks and has no clock time (`planning.asks_for_plan`) places the open tasks (or only the ones named) into the free gaps of the calendar: window 09:00-21:00, from the next quarter hour today, 10 minutes apart, default durations, spilling to at most two more days.
2. The result is ONE ordinary multi-event add preview with a 'taslak plan' note and the tasks that did not fit; nothing is written before 'evet' (ADR 0002/0004). Saving an event titled like a task closes that task (existing behaviour).
3. A preview already open is never replaced; the message falls through to the older flows.

## Training
Round 23 fixtures: `tests/conformance/training/planning.json` (real incident first), `tests/test_planning.py` (placement).
