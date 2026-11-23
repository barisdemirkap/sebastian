# 0004. Natural-language layer, pending actions and the "evet" gate

Status: accepted (owner decision, 2026-11-18). Ported in design from the owner's other bot (language model as parser only).

## Context
The owner writes or speaks Turkish freely. Rules alone cover only fixed phrasings; a language model understands loose speech but must never be the authority over his calendar.

## Decisions
1. **The model only classifies.** One-shot `claude -p` (sonnet, no tools, no MCP, no session persistence, no project memory, empty temp cwd, secrets stripped from its environment, message on stdin) returns an intent JSON. `nl.validate` checks it against a fixed schema: `add_event`, `edit_event`, `move_event` (normalized to edit), `delete_event`, `list_events`, `birthdays`, `help`, `chitchat`, `unclear`. Unknown intent, wrong type, out-of-range value: rejected. Unknown keys are never copied. The model's output, the owner's message and event titles are all untrusted data.
2. **Never invent.** A date or time the model returns is dropped when the owner's words (and earlier clarification answers) contain no date or time at all; the assistant then asks. Hours 1-6 without a day part or explicit minutes ("3'te") are asked ("03:00 mi, 15:00 mi?"). Default duration 60 minutes is visible in the preview.
2a. **Chain**: `claude`, then local Qwen (`http://127.0.0.1:8090/v1`), then the minimal deterministic parser (`rules.py`, `timeparse.py`: commands and simple Turkish patterns). Each provider is tried once per message; failure or timeout moves on. A provider that answers with JSON that breaks the schema ends the chain and the owner is asked to rephrase.
3. **Reads are computed in code** from the calendar (today, tomorrow, week, a date, birthdays); text comes from `i18n.py`. The model supplies no number or event shown to the owner.
4. **Writes use pending actions.** A write intent stores a row in SQLite `pending_actions` (intent, args JSON, SHA-256 of intent+args, created, expires after 10 minutes, status) and the owner sees a code-rendered preview ending in `(evet / hayır)`. Only a bare `evet` executes the STORED arguments, after the TTL check and a hash recomputation; any mismatch refuses and writes nothing. `hayır`/`vazgeç` cancels; any other text drops the preview and is handled as a new request; with no open row `evet` writes nothing. A new row supersedes the older open one. Deleting needs the stronger `evet sil` with extra wording; a bare `evet` on a delete preview explains and deletes nothing.
5. **Ask until sure.** Missing title, date or time (or an ambiguous event among several) become one short question at a time, kept in the `conversation` row (survives restart): 15-minute TTL, 5-reply cap, cancel words. With the model on, the answer goes back to it with the question and the partial intent; code still validates. Several matching events are listed and chosen by number. Matching of event names is fuzzy (folded Turkish, suffix tolerant).
6. **Conflicts** are warned about in the preview (timed events overlapping the new time).
7. **Consent, cap, audit.** `ALFRED_NL=off|claude|qwen` (default off) and `ALFRED_NL_CONSENT=1` (the owner's opt-in; without it no provider runs and nothing leaves the machine). `ALFRED_NL_DAILY_CAP` (default 60 calls/day). One audit row per model call (provider, model, intent, duration; no message text) and one per pending, write, blocked write or error.
8. **Single user.** Only `ALFRED_OWNER_CHAT_ID` is served; every other chat gets no reply and no processing. The id is never hard-coded.

## Consequences
Messages go to the model provider when `claude` is used (the owner consents by env); `qwen` stays local. A bad model answer costs at most a wrong preview that the owner declines.
