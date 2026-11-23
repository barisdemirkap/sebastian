# 0008. Conversation conformance suite

Status: accepted (owner decision, 2026-11-19). Ported from the Acme suite (copied and adapted; Alfred imports nothing from Acme).

## Context
The 2026-11-18 live failure ("onayliyorum" then "evet", ADR 0006) was one of a class: wording the bot did not know, a preview lost to chit-chat, the same reply twice. One-off tests fix one wording at a time.

## Decisions
1. **Bot-agnostic suite in `tests/conformance/`.** `invariants.py` and `phrasebook_tr.py` import no Alfred code. `alfred_adapter.py` is the only Alfred-specific file: it wires the real `Assistant`, the real `Bot` loop (dedupe, debounce, keyboards), a fake Telegram transport that ignores the poll offset (so redelivery really reaches the bot), a fake model provider and the fixture calendar to the adapter contract.
2. **Invariants asserted after every turn.** I1 no identical bot text twice in a row (slash commands exempt); I2 an open preview survives every message that is not an explicit confirm, cancel or edit; I3 a pure affirmation saves exactly the previewed events once, a negation cancels, an edit never saves; I4 no write without a confirmed preview; I5 a fact the owner stated is never asked again; I6 redelivery and bursts never duplicate replies or writes; I7 single owner: any other chat gets no reply and changes nothing; I8 no placeholder in a reply; I9 an event disappears only through a confirmed delete preview, and only the previewed one; I10 (ADR 0011) a yes/no style question is only sent while a matching preview or open clarification exists.
3. **Phrasebook.** At least 60 affirmations and 30 negations with seeded spelling variants (diacritics, case, punctuation, emoji, stretched letters), plus edits, chit-chat, read questions, gibberish and frustration. Used for property-style tests over five previews: single add, bulk import, model-driven, edit, delete.
4. **Alfred policy in the adapter.** A bare yes on a DELETE preview ("evet", "tamam", thumbs-up) is only a hint and keeps the preview (ADR 0006, decision 2); "sil", "evet sil" and "onaylıyorum" delete. The adapter's `expected_kind` encodes this; the invariants stay generic.
5. **Incident fixtures** (`incidents/*.json`, fictional data): the import-preview approval, slot loops, frustration, gibberish, a burst, network stall and redelivery, another chat, a bare yes on a delete, an edit that is not a confirmation, hesitation, repeated replies.
6. **Working rule (CLAUDE.md):** every bug becomes an incident fixture first, then the fix.

## Defects the first run found and fixed
- "eee" (a hesitation) collapsed to the colloquial "e" and SAVED the event; "e" is no longer a yes. "hh" collapsed to "h" and cancelled the preview; "h" is no longer a no.
- Unknown wordings for yes (yaz, kaydetsin, kaydedelim, tmm, evt, "iyi kaydet", "aynen öyle", "evet kardeş") and no (kaydetme, yazma, kalsın, "hayır kardeş").
- The same unclear reply or delete hint twice in a row: a changing lead is put in front (`i18n.REPEAT_LEADS`, `Assistant._no_repeat`).
