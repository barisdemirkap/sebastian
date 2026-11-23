# Roadmap

Each step gets its own ADR and the owner's approval before it starts.

1. **Core extraction**: split the generic core from owner-specific data; one config file for persona, language, timezone, providers and permissions.
2. **Plugin architecture**: calendar, tasks, lists first; each plugin can be switched on or off. Writes stay preview + confirm.
2b. **Channel adapters** (ADR 0002): Telegram, Slack (Socket Mode), CLI first; same preview + confirm gate on every channel.
3. **Language packs**: Turkish first, English next.
4. **Fork kit**: install guide, example config with fictional data, tests, contribution notes.
5. **Migration**: keep the private assistant running while Sebastian is built piece by piece; replace it when ready.
6. **Later, each its own decision**: mail awareness, other integrations (evaluate ready-made connectors such as MCP first).
