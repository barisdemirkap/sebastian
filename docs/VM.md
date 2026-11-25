# Run Sebastian on a VM

Sebastian is not a hosted service: you run your own copy on a machine you control (laptop, home server, cheap VM). Nothing leaves it except calls to the platforms you configure.

## Docker (any VM)
See [QUICKSTART.md](QUICKSTART.md). Short form:
```
git clone <your fork> && cd sebastian
cp .env.example .env && chmod 600 .env && $EDITOR .env
docker compose run --rm sebastian init --timezone Europe/Istanbul     # writes config.toml into the sebastian-data volume
docker compose run --rm sebastian doctor
docker compose up -d
```
Bot tokens go into the volume's `secrets/` directory (chmod 600), e.g. `docker compose run --rm -v "$PWD/telegram.token:/tmp/t:ro" --entrypoint sh sebastian -c 'mkdir -p ~/.config/sebastian/secrets && install -m 600 /tmp/t ~/.config/sebastian/secrets/telegram.token'`.

## systemd (Linux, no Docker)
`python -m venv .venv && .venv/bin/pip install . websockets`, then install `deploy/sebastian.service` (see the comments in it).

## macOS
launchd templates are in `deploy/*.plist.template`.

## Notes
- Back up `sebastian.db` and the secrets directory; they are your whole state.
- Model: set `SEBASTIAN_NL` and your own API key in `.env` (see QUICKSTART.md). The tool-using agent needs the `claude` CLI on the same machine; without it Sebastian uses its offline rules.
- Status: these files are written for the standard paths but not yet tested on a fresh VM.
