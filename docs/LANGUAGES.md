# Languages

Sebastian's replies come from a language pack chosen in the config (ADR 0006):

```toml
[persona]
language = "en"      # "tr" (default) or "en"
```

An unknown value stops start-up with a config error that lists the available packs. `sebastian init --language en` writes a starter config for English, and `sebastian doctor` shows the active language.

## What this covers and what it does not
- Covered: every reply, digest, preview, button label, help text, reminder and the "this feature is switched off" refusal, plus date formatting.
- Understanding English (ADR 0008): works in every reply language, additively next to Turkish. Offline rules read:
  - dates and times: "tomorrow at 3pm", "next Friday 10:30", "on 5 March", "March 5th at noon", "in 3 days", "in 2 hours", "from 12pm to 1:30pm", "for 45 minutes", "remind me 30 minutes before", "every Monday";
  - adding events: "add dentist tomorrow at 3pm", "schedule lunch with Anna on 5 March", "book a meeting with Sam next Friday 10:30", "remind me to pay rent tomorrow at 9am", "add Anna's birthday on 3 March"; a missing day or time is asked, and every write keeps preview + confirm;
  - questions: "what's on today", "what's on tomorrow", "what do I have this week", "am I free on Friday", "what's coming up";
  - shopping and named lists: "add milk and eggs to my shopping list", "put bread on the list", "what's on my list", "remove milk from the shopping list", "clear my shopping list", "I bought milk", "we are out of olive oil";
  - `help`, `commands`, "yes" / "confirm" / "cancel" / "no thanks", `add task buy milk`, `task: call the plumber`, `my tasks`, and the English slash commands.
- Not covered in English: moving, changing or deleting an existing event; task statements beyond `add task` / `my tasks`; several statements in one message; purchase phrasings like "we need milk"; recurrence beyond daily, weekly and "every <weekday>"; "midnight" and "half past three"; numeric `03/05` dates; the model prompts. Use agent mode (a model understands English) or the Turkish wording for these. The ADR lists the reading choices ("next Friday" means that day of next week; "at 3" asks 03:00 or 15:00).
- Titles, notes and list items keep whatever language you write them in.
- `[persona] owner_title` is the only form of address the replies use. The English pack no longer mixes in "sir"; with the Turkish pack the default rotation keeps "Sir" and "Master" only while `owner_title` is the fictional default.

## Add a language
1. Copy `src/sebastian/languages/en.py` to `<code>.py` (use the ISO 639-1 code, for example `de.py`) and translate the values. Keep every key and every `{placeholder}` exactly as in the original (`{s}` is the form of address, `{ack}` the acknowledgement). Fill in the other `Pack(...)` fields: forms of address (an entry equal to `default_owner_title` follows the user's `owner_title`; list any form that assumes the owner, such as "sir", in `gendered_forms` so a custom title replaces it; `strip_forms` are removed from model text only), `list_aliases` (how the owner names each list, read by the English reader), acknowledgements, day and month names, flags, plugin and list titles, and `recall_marker`, which must appear in your `recall_last` and `recall_at` templates. Set `agent_note` to tell the model to reply in your language.
2. Register it in `src/sebastian/languages/__init__.py` (`PACKS`).
3. Run `uv run pytest -q tests/test_language_packs.py`. It fails with the list of keys you have not translated, with placeholders that differ from the base pack, and with structural gaps. The base pack, `tr`, is the reference; a new key is added there first.
4. Mention the language in `config.example.toml` and this file.

Wording style is part of the voice (ADR 0018): formal, calm, short, no jokes, no emojis except list markers.
