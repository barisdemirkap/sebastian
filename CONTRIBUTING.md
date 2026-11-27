# Contributing

Sebastian is a single-user assistant: you run your own copy. Contributions that make forking, running and extending it easier are welcome. English only in code, docs, issues and commit messages (the Turkish reply pack and Turkish test phrases are data, not prose).

## Dev setup
```
git clone https://github.com/barisdemirkap/sebastian && cd sebastian
uv sync --extra dev          # or: python -m venv .venv && .venv/bin/pip install -e '.[dev]'
uv run sebastian chat        # offline demo, fixture calendar
uv run pytest -q             # the whole suite, offline, about 10 seconds
```
Python 3.11 or newer. The project has no runtime dependencies on purpose; think twice before adding one.

## Rules that never bend
1. **Preview + confirm.** Anything that writes (calendar, messages, bulk list changes) is rendered as a preview by code and needs an explicit confirmation on every channel. A new feature must not add a path around that gate. See ADR 0001 and 0004.
2. **No live network in tests.** Tests use fake transports, the fixture calendar, fake model providers and `tests/cassettes`. A test that needs a real account, token or internet is rejected. Live access in the product is behind `SEBASTIAN_ALLOW_LIVE=1`.
3. **No private data.** Fictional names, ids, domains and phone numbers only (`example.com`, `1000000001`). Secrets never go in the repo; see [SECURITY.md](SECURITY.md).

## ADR-first process
If a change alters behaviour, a boundary or a default, write a short ADR in `docs/adr/` (next number, Context / Decision / Consequences, see the existing ones) and link it from [docs/ROADMAP.md](docs/ROADMAP.md) when it is a roadmap step. Open an issue to discuss first. Bug fixes, docs and tests need no ADR. Add tests with the change; keep the suite green.

## Add a channel, a language or a plugin
- **Channel** (Telegram, Slack and console exist): implement the contract in `src/sebastian/channels/base.py`, register it in `channels/factory.py`, add fake-transport tests, write a `docs/<NAME>-SETUP.md`. Design and limits: [ADR 0002](docs/adr/0002-channel-adapters.md); examples: [docs/TELEGRAM-SETUP.md](docs/TELEGRAM-SETUP.md), [docs/SLACK-SETUP.md](docs/SLACK-SETUP.md).
- **Language**: copy a reply pack, translate, register, run the parity test. Steps: [docs/LANGUAGES.md](docs/LANGUAGES.md), design: [ADR 0006](docs/adr/0006-language-packs.md).
- **Plugin**: a registry entry in `src/sebastian/plugins.py` with its commands, intents, tools and digest sections, its own `[plugins]` switch (default off for new features), and tests for the disabled path. Design and the "no third-party code loader yet" limit: [ADR 0005](docs/adr/0005-plugin-architecture.md).

## Pull requests
Small and focused, tests included, `uv run pytest -q` green, ADR linked when there is one. CI runs the same suite offline. Be kind and assume good faith; harassment is not tolerated, and the maintainer may remove comments or contributors that cross that line.
