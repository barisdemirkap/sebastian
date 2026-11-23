#!/usr/bin/env bash
# Starts the bot in the foreground. Loads KEY=VALUE lines from ~/.config/sebastian/env (0600), if present.
# Used by the launchd template; the owner runs it by hand for a first try. Nothing live happens unless the env
# file sets SEBASTIAN_ALLOW_LIVE=1.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${SEBASTIAN_ENV_FILE:-$HOME/.config/sebastian/env}"
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
export PYTHONPATH="$PWD/src"
exec "${SEBASTIAN_PYTHON:-$PWD/.venv/bin/python}" -m sebastian.cli bot
