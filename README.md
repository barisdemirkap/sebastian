# Sebastian

A personal AI assistant for **one person**. Fork it, run it on your own machine, configure it, extend it, and stay in control.

Status: early. Sebastian grows out of a private assistant ("Sebastian"); the generic core is being extracted step by step.

## Quickstart (about a minute, offline, no account)
```
git clone https://github.com/barisdemirkap/sebastian && cd sebastian && uv sync
uv run sebastian init --language en && uv run sebastian doctor
uv run sebastian chat             # try: /help, /today, "add task buy milk"  (Ctrl+D quits)
```
No `uv`? Use `python -m venv .venv && .venv/bin/pip install -e .` and run `.venv/bin/sebastian ...` instead. `chat` works even before `init`: it uses a fixture calendar and saves nothing. `init` writes `~/.config/sebastian/config.toml` (add `--timezone Europe/London --title "Ms. Doe"`); replies are Turkish unless you pass `--language en` (English input works either way).

Next: connect a channel (Telegram: [docs/TELEGRAM-SETUP.md](docs/TELEGRAM-SETUP.md), Slack: [docs/SLACK-SETUP.md](docs/SLACK-SETUP.md)) in `[channel]` of your config, run it on a VM ([docs/VM.md](docs/VM.md)) or in a container ([docs/QUICKSTART.md](docs/QUICKSTART.md)), and extend it ([CONTRIBUTING.md](CONTRIBUTING.md): channels, languages, plugins). Security and secrets: [SECURITY.md](SECURITY.md).

## Principles
- **Single user, local first.** No hosted multi-user service. Your data, your secrets, your machine.
- **You stay in control.** Anything that writes (calendar, tasks, messages) is shown as a preview and needs your explicit confirmation. Nothing is sent to a third party without it.
- **Everything is configurable.** Persona, reply language (Turkish or English), timezone, providers and permissions live in one config file.
- **Plugins, not a monolith.** Calendar, tasks, lists, mail and more are separate plugins you switch on or off.
- **Forkable.** Secrets live outside the repository; examples use fictional data.

## Roadmap
See [docs/ROADMAP.md](docs/ROADMAP.md) and the decisions in [docs/adr](docs/adr).

## License
MIT. See [LICENSE](LICENSE).

## Current state
The first code drop is an anonymized copy of the generic parts of a private assistant (calendar with preview-gated writes, tasks, lists, reminders, digests, Telegram, optional model agent mode, ~4100 offline tests). Names and identifiers in the test data are fictional. Features (calendar, tasks, lists, reminders, digests) are plugins you can switch off in the `[plugins]` config section (ADR 0005). Replies come in Turkish (default) or English via `[persona] language` (ADR 0006, [docs/LANGUAGES.md](docs/LANGUAGES.md)); English input (ADR 0008) covers adding events, calendar questions and shopping lists next to Turkish; changing or deleting events in English words still needs agent mode. The fork kit (ADR 0007) is in place: contribution guide, security notes, templates and offline CI. Design notes from the private assistant are in `docs/legacy/`.
