# 0003. Persona lives in the config file

Status: accepted (roadmap step 1, owner-approved plan 2026-11-23).

## Decision
`[persona]` in `config.toml` sets `owner_title` (the form of address) and `assistant_name`. `i18n.configure()` applies them to the form-of-address rotation, the address-stripping patterns and the agent system prompt; the CLI calls it when it loads the config. Defaults are fictional ("Ahmet Bey", "Sebastian"). Tone, language and other wording stay in the `i18n` block until language packs (roadmap step 3).

## Consequences
- No owner-specific string is needed in code; a fork only edits its own config.
- The agent prompt version is computed lazily so a persona change rotates the model session.
