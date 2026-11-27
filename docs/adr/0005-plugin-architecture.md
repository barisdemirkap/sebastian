# 0005. Plugin architecture: a registry of switchable features

Status: accepted (owner-delegated roadmap step 2, 2026-11-27). Builds on ADR 0001 and ADR 0002.

## Context
Calendar, tasks, lists, reminders and digests all live in one core. A forker may want only some of them (tasks and lists without any calendar account, or a calendar without nagging). Today they cannot be turned off without editing code.

## Decision
- A plugin is a description, not a loaded module: `plugins.py` holds a registry of `Plugin(name, commands, intents, tools, digest_sections, requires)`. The code stays in the core; the registry says which commands, language-layer and pending-write intents, agent-mode tools and digest sections belong to which feature. A dynamic loader for third-party code is NOT built; it needs its own ADR (trust and sandboxing).
- First plugins: `calendar`, `tasks`, `lists`, `reminders` (requires `calendar`), `digests`. Anything not owned by a plugin (help, notes, persona, health, channels) is core and always on.
- Config: a `[plugins]` section with `name = true|false`. Absent means on, so existing configs behave exactly as before. Unknown names and non-boolean values are config errors.
- A disabled plugin refuses its slash commands, its wordings (`Assistant._plugin_gate`), its intents (`_dispatch`) and its agent tools with one friendly message that names the switch, and it drops out of the digests, reminders and the task nag. A disabled calendar is never fetched. The gate only runs when something is switched off, so the default path is untouched.
- Writes do not change: an enabled plugin still writes only through preview + confirm. As a safety net a pending preview whose plugin was switched off after it was shown is refused at confirm time and closed as blocked.

## Consequences
- Wording-level gating is best effort: free-form statements are classified by the existing parsers, so an odd phrasing for a disabled feature may still reach it. The hard guarantees are the write-time refusals (`_confirm`, agent tools, import and list CLI) and the scheduler. Immediate task and list writes that do not use a preview are covered by the wording gate and the agent-tool gate only.
- Agent mode still advertises all tools to the model (the tool schema and its hash stay stable); a disabled tool returns `PLUGIN_DISABLED` instead of acting.
- `sebastian doctor` shows which plugins are on.
- New features (mail awareness, other integrations) should arrive as registry entries with their own switch, default off until the owner approves.
