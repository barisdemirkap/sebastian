# Security

## Trust model
Sebastian is built for **one person on their own machine** (ADR 0001, 0004). There is no hosted service, no multi-user mode and no shared database. The owner is the only identity that matters:
- Only the configured owner chat on the configured channel is served; every other sender is ignored.
- Anything that writes is previewed and needs the owner's explicit confirmation.
- Nothing is sent to a model provider until `SEBASTIAN_NL_CONSENT=1`; with `SEBASTIAN_NL=off` (default) nothing leaves the machine.
- Real accounts and channels are off unless `SEBASTIAN_ALLOW_LIVE=1`.

Out of scope: protecting against someone who already has your user account on the machine, or running Sebastian as a service for other people.

## Secrets and where they live
Never in the repository, never in the config file's committed form, never in logs.
- Model API keys: environment only (`.env` with mode 0600 for the container, see `.env.example`).
- Channel tokens and OAuth client secrets and refresh tokens: files under `~/.config/sebastian/secrets/` (mode 0600), for example `telegram.token`, `google.client_secret`.
- Config: `~/.config/sebastian/config.toml` (ignored by git); the database `sebastian.db` holds your messages, tasks and lists locally.
- `sebastian doctor` reports whether keys are set and whether permissions are too open, without printing values.
If a secret leaks, revoke it at the provider first, then replace the file.

## Reporting a vulnerability
Please do not open a public issue for a security problem. Use GitHub's private vulnerability reporting on this repository (Security tab, "Report a vulnerability") if it is enabled; otherwise open a public issue that says only "security contact requested" with no details, and the maintainer will reach out. Include what you found, how to reproduce it and the affected version. Expect an acknowledgement within a few days; this is a small personal project and there is no bounty.
