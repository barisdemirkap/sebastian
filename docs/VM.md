# Run Sebastian on a VM

Sebastian is not a hosted service: you run your own copy on a machine you control (laptop, home server, cheap VM). Nothing leaves it except calls to the platforms you configure.

## Docker (any VM)
```
git clone <your fork> && cd sebastian
mkdir -p data/.config/sebastian/secrets
docker compose run --rm sebastian init --timezone Europe/Istanbul     # writes data/.config/sebastian/config.toml
$EDITOR data/.config/sebastian/config.toml                             # set [channel] kind = "telegram" or "slack"
cp your-bot-token data/.config/sebastian/secrets/telegram.token && chmod 600 data/.config/sebastian/secrets/telegram.token
SEBASTIAN_OWNER_CHAT_ID=<your id> docker compose run --rm sebastian doctor
SEBASTIAN_OWNER_CHAT_ID=<your id> docker compose up -d
```

## systemd (Linux, no Docker)
`python -m venv .venv && .venv/bin/pip install . websockets`, then install `deploy/sebastian.service` (see the comments in it).

## macOS
launchd templates are in `deploy/*.plist.template`.

## Notes
- Back up `sebastian.db` and the secrets directory; they are your whole state.
- The optional model agent needs the `claude` CLI on the same machine; without it Sebastian uses its offline rules.
- Status: these files are written for the standard paths but not yet tested on a fresh VM.
