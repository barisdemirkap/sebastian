# Roadmap

Each step gets its own ADR and the owner's approval before it starts.

1. **Core extraction**: split the generic core from owner-specific data; one config file for persona, language, timezone, providers and permissions.
2. **Plugin architecture** (ADR 0005, done): calendar, tasks, lists, reminders and digests are registry entries, each switched on or off in `[plugins]` (all on by default). Writes stay preview + confirm. Not built: a loader for third-party plugin code.
2b. **Channel adapters** (ADR 0002, done): Telegram, Slack (Socket Mode), CLI first; same preview + confirm gate on every channel.
3. **Language packs** (ADR 0006, done): replies live in selectable packs, Turkish (default) and English, chosen by `[persona] language`; a parity test keeps packs complete ([docs/LANGUAGES.md](LANGUAGES.md)). English understanding followed in ADR 0008 (below). Not built: an English agent prompt.
3b. **English input** (ADR 0008, done): English dates and times, add-event wording, calendar questions and shopping-list wording, additive next to the Turkish readers and accepted in every reply language; plain `help`; a custom `owner_title` is now the only form of address. Not built: moving, changing or deleting events in English, English task statements, clause splitting and purchase phrasings, an English agent prompt ([docs/LANGUAGES.md](LANGUAGES.md)).
4. **Fork kit** (ADR 0007, done): three-command offline quickstart, `CONTRIBUTING.md`, `SECURITY.md`, issue and PR templates, offline CI, fictional example data, a leakage audit. Not built: a release process and GitHub repository settings (owner decisions).
5. **Migration**: keep the private assistant running while Sebastian is built piece by piece; replace it when ready.
6. **Later, each its own decision**: mail awareness, other integrations (evaluate ready-made connectors such as MCP first).
