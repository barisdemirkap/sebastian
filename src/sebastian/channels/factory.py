"""Build the configured channel (`[channel] kind` in the config file). Tokens are read from the secrets directory, never from the config."""
from __future__ import annotations
from pathlib import Path

from ..config import Config


def make_channel(c: Config, secrets: Path, owner_chat: int):
    kind = c.channel.kind
    if kind == "console":
        from .console import ConsoleChannel
        import sys
        return ConsoleChannel(owner=owner_chat, read=lambda: sys.stdin.readline().rstrip("\n") or None)
    if kind == "slack":
        from .slack import SlackChannel, web_api_caller
        from .slack_socket import SocketModeEvents
        bot, app = secrets / c.channel.slack_bot_token_file, secrets / c.channel.slack_app_token_file
        for f in (bot, app):
            if not f.exists():
                raise SystemExit(f"Slack token file not found: {f}")
        token = bot.read_text().strip()
        return SlackChannel(owner_chat, c.channel.slack_user, web_api_caller(token), SocketModeEvents(app.read_text().strip()))
    from ..telegram import TelegramApi
    f = secrets / (c.telegram.token_file or "telegram.token")
    if not f.exists():
        raise SystemExit(f"Telegram token file not found: {f}")
    return TelegramApi(f.read_text().strip())
