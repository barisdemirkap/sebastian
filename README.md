# Sebastian

A personal AI assistant for **one person**. Fork it, run it on your own machine, configure it, extend it, and stay in control.

Status: early. Sebastian grows out of a private assistant ("Sebastian"); the generic core is being extracted step by step.

## Quickstart (about a minute, offline)
```
git clone https://github.com/barisdemirkap/sebastian && cd sebastian
python -m venv .venv && .venv/bin/pip install -e .
.venv/bin/sebastian chat          # talk to it now: fixture calendar, no accounts, nothing saved
.venv/bin/sebastian init          # write your own config (timezone, how it addresses you)
.venv/bin/sebastian doctor        # what is missing before connecting a channel
```
Connect a channel (Telegram: [docs/TELEGRAM-SETUP.md](docs/TELEGRAM-SETUP.md), Slack: [docs/SLACK-SETUP.md](docs/SLACK-SETUP.md), or the console) in `[channel]` of your config; run on a VM with [docs/VM.md](docs/VM.md).
Fork-and-run, container and model choice: [docs/QUICKSTART.md](docs/QUICKSTART.md).

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
The first code drop is an anonymized copy of the generic parts of a private assistant (calendar with preview-gated writes, tasks, lists, reminders, digests, Telegram, optional model agent mode, ~3900 offline tests). Names and identifiers in the test data are fictional. Features (calendar, tasks, lists, reminders, digests) are plugins you can switch off in the `[plugins]` config section (ADR 0005). Replies come in Turkish (default) or English via `[persona] language` (ADR 0006, [docs/LANGUAGES.md](docs/LANGUAGES.md)); understanding free-text commands is still Turkish-first. Next: the fork kit. Design notes from the private assistant are in `docs/legacy/`.
