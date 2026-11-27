# 0006. Language packs: replies are selectable, understanding stays Turkish-first

Status: accepted (owner-delegated roadmap step 3, 2026-11-27). Builds on ADR 0003 (persona in config) and ADR 0005.

## Context
All owner-facing text lived in one Turkish block in `i18n.py`, next to the Turkish wording vocabulary the parsers match. A forker who does not read Turkish could not get replies in their language, and nothing told a contributor which strings a new language must cover.

## Decision
- A language pack is a module in `sebastian/languages/` that exports one `Pack` (`pack.py` documents each field): the reply templates (`messages`), forms of address and acknowledgements, the "repeating myself" leads and alternative question wordings, day and month names, digest flags, the plugin names used by the plugin-disabled refusal, the display titles of the named lists, the marker that identifies a read-back reply, and an optional note appended to the agent-mode prompt. Packs are registered in `languages/__init__.py`.
- Shipped packs: `tr` (default; wording unchanged) and `en`.
- Config: `[persona] language = "tr" | "en"` (default `"tr"`). It is part of `[persona]` because it shapes the voice; ADR 0003 already said language would move to packs. An unknown value is a `ConfigError` naming the available packs. A missing `owner_title` falls back to the selected pack's fictional default ("Ahmet Bey" for `tr`, "Mr. Smith" for `en`). `i18n.configure(owner_title, assistant_name, language)` selects the pack; the CLI calls it after loading the config. `sebastian init --language en` writes it, `sebastian doctor` shows it.
- `i18n.t()`, the date helpers, `TR` (now the active pack's messages, refilled in place), and the other pack data are read through `i18n`, so call sites did not change. The English pack uses English spellings of the slash commands (`/help`, `/today`, `/tasks`, `/cancel`, ...); the Turkish spellings keep working in every language.
- Parity is enforced by tests (`tests/test_language_packs.py`): every pack must have exactly the message keys of the base pack, the same `str.format` placeholders per key, the same dictionary keys for flags, plugin titles, list titles, day words and question variants, 7 days and 12 months. A contributor who adds a language gets one failing test per missing key.

## What is translated and what is not (honest limits)
- Translated: every reply, digest, preview, button label, help text, reminder, the plugin-disabled refusal, date and duration formatting.
- Not translated, still Turkish-first:
  - The inbound wording parsers (tasks, lists, dates and times, "move/delete" phrasings, clause splitting, the free-text confirmation vocabulary in `i18n.py`). Date and time parsing in particular ("yarin 15:00", "cumaya") is Turkish.
  - The model prompts in `nl.py`, `assist_prompt.md` and the agent-mode prompt. For `en` the agent prompt gets a note telling the model to answer in English; the model itself understands English messages, so agent mode (the owner-trial path) works in English, but the classic offline rules do not.
  - Slash commands keep their Turkish spellings as the canonical names.
- English inbound added in this step (small, tested, accepted in every language, additive): confirm and cancel words (`yes`, `confirm`, `approve`, `yep`, `cancel`, `nope`, `stop`, `delete`/`remove` for delete confirmation), `add task X` / `task: X` / `todo: X`, `my tasks` / `task list`, and the English slash-command aliases.
- Superseded by ADR 0008 (English input, built afterwards). At the time of this step: English "add event" and shopping-list phrasings. They need an English date and time parser (today, tomorrow, weekdays, 3pm, 15:00, ranges, recurrence) and English forms of every list and edit phrasing; that is a parser project, not a wording swap. English users get these through agent mode (model-based understanding) or by waiting for a follow-up ADR on an English parser.

## Consequences
- Adding a reply language is one new module plus one registry line; the tests say what is missing. Adding English-first understanding is a separate, larger step.
- Mixed-language data is possible by design: titles and notes keep the owner's own words in any language.
- The base pack (`tr`) is the reference: a new key is added there first, then to every other pack, or the parity test fails.
