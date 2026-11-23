# Sebastian

A personal AI assistant for **one person**. Fork it, run it on your own machine, configure it, extend it, and stay in control.

Status: early. Sebastian grows out of a private assistant ("Alfred"); the generic core is being extracted step by step.

## Principles
- **Single user, local first.** No hosted multi-user service. Your data, your secrets, your machine.
- **You stay in control.** Anything that writes (calendar, tasks, messages) is shown as a preview and needs your explicit confirmation. Nothing is sent to a third party without it.
- **Everything is configurable.** Persona, language, timezone, providers and permissions live in one config file.
- **Plugins, not a monolith.** Calendar, tasks, lists, mail and more are separate plugins you switch on or off.
- **Forkable.** Secrets live outside the repository; examples use fictional data.

## Roadmap
See [docs/ROADMAP.md](docs/ROADMAP.md) and the decisions in [docs/adr](docs/adr).

## License
MIT. See [LICENSE](LICENSE).
