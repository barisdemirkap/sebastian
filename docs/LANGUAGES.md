# Languages

Sebastian's replies come from a language pack chosen in the config (ADR 0006):

```toml
[persona]
language = "en"      # "tr" (default) or "en"
```

An unknown value stops start-up with a config error that lists the available packs. `sebastian init --language en` writes a starter config for English, and `sebastian doctor` shows the active language.

## What this covers and what it does not
- Covered: every reply, digest, preview, button label, help text, reminder and the "this feature is switched off" refusal, plus date formatting.
- Not covered: how Sebastian understands what you type. The classic offline rules, the date and time parser, and the model prompts are Turkish-first. In every language these English inputs work: yes, confirm, approve, cancel, no, stop, "yes delete", `add task buy milk`, `task: call the plumber`, `my tasks`, and the English slash commands (`/help`, `/today`, `/tomorrow`, `/week`, `/tasks`, `/list`, `/cancel`, ...). English event and shopping-list phrasings ("add dentist tomorrow 3pm") are not parsed by the offline rules; use agent mode (a model understands English) until an English parser exists. See the ADR for why.
- Titles, notes and list items keep whatever language you write them in.

## Add a language
1. Copy `src/sebastian/languages/en.py` to `<code>.py` (use the ISO 639-1 code, for example `de.py`) and translate the values. Keep every key and every `{placeholder}` exactly as in the original (`{s}` is the form of address, `{ack}` the acknowledgement). Fill in the other `Pack(...)` fields: forms of address (the entry equal to `default_owner_title` follows the user's `owner_title`), acknowledgements, day and month names, flags, plugin and list titles, and `recall_marker`, which must appear in your `recall_last` and `recall_at` templates. Set `agent_note` to tell the model to reply in your language.
2. Register it in `src/sebastian/languages/__init__.py` (`PACKS`).
3. Run `uv run pytest -q tests/test_language_packs.py`. It fails with the list of keys you have not translated, with placeholders that differ from the base pack, and with structural gaps. The base pack, `tr`, is the reference; a new key is added there first.
4. Mention the language in `config.example.toml` and this file.

Wording style is part of the voice (ADR 0018): formal, calm, short, no jokes, no emojis except list markers.
