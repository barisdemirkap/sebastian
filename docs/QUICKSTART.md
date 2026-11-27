# Fork and run

Sebastian is for one person. You run your own copy; nobody else sees your data or your API key.

## Offline, no account (1 minute)
```
git clone <your fork> && cd sebastian && uv sync
uv run sebastian init --language en     # optional: writes ~/.config/sebastian/config.toml
uv run sebastian doctor                 # what is missing before connecting a channel
uv run sebastian chat                   # fixture calendar, nothing saved; try /help, /today; Ctrl+D quits
```
Without `uv`: `python -m venv .venv && .venv/bin/pip install -e .`, then `.venv/bin/sebastian ...`. Developers: `uv sync --extra dev && uv run pytest -q` (see [CONTRIBUTING.md](../CONTRIBUTING.md)).

## Container, one command
```
cp .env.example .env && chmod 600 .env     # then edit .env: your own API key, consent, your chat id
docker compose up -d
```
First run only, write a config into the data volume and check it:
```
docker compose run --rm sebastian init --timezone Europe/Istanbul
docker compose run --rm sebastian doctor
```
Your data lives in the named volume `sebastian-data` (config, secrets, `sebastian.db`). Back it up. `docker compose down` keeps it; `docker compose down -v` deletes it.

## Choosing the model (`SEBASTIAN_NL` in `.env`)
| Value | Backend | Key |
|---|---|---|
| `off` | offline rules only (default) | none |
| `anthropic` | Anthropic API | `ANTHROPIC_API_KEY` |
| `openai` | OpenAI API | `OPENAI_API_KEY` |
| `claude` | the `claude` CLI login (not available inside the container image) | none |
| `qwen` | local OpenAI-compatible server (`SEBASTIAN_NL_QWEN_URL`) | none |

Rules: the key comes from the environment only and is never logged or printed. Nothing is sent to a provider until `SEBASTIAN_NL_CONSENT=1`. Optional `SEBASTIAN_NL_MODEL` overrides the model name. `sebastian doctor` reports whether the key is set and whether `.env` is too open, without showing the key. Agent mode (tools) still needs the `claude` CLI.

Why one instance per user and no hosted service: [ADR 0004](adr/0004-isolated-instance-per-user.md). More hosting options: [VM.md](VM.md).
