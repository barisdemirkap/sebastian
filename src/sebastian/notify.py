"""Delivery. Telegram is the target channel; the default notifier is a stub that sends nothing."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from .http import Transport


class Notifier(Protocol):
    def send(self, text: str) -> None: ...


class StubNotifier:
    """Records messages in memory (and optionally to a JSONL outbox). Never touches the network."""

    def __init__(self, outbox: Path | None = None):
        self.sent: list[str] = []
        self.outbox = outbox

    def send(self, text: str) -> None:
        self.sent.append(text)
        if self.outbox:
            self.outbox.parent.mkdir(parents=True, exist_ok=True)
            with self.outbox.open("a") as f:
                f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "text": text}) + "\n")


class TelegramNotifier:
    """Sends to ONE configured owner chat via the Telegram Bot API (the Cimcime bot).

    Only enabled after owner approval; the chat id is fixed in config, never taken from input."""

    def __init__(self, bot_token: str, chat_id: str, transport: Transport):
        self._token, self.chat_id, self.transport = bot_token, chat_id, transport

    def send(self, text: str) -> None:
        for chunk in split_message(text):
            self.transport.request("POST", f"https://api.telegram.org/bot{self._token}/sendMessage",
                                   form={"chat_id": self.chat_id, "text": chunk})


class LoggingNotifier:
    """Wraps another notifier and records every successfully sent message in the message log, so the bot can read back
    what the scheduler sent (ADR 0010). A failed send is not logged."""

    def __init__(self, inner: Notifier, store, chat_id: int, clock):
        self.inner, self.store, self.chat_id, self.clock = inner, store, chat_id, clock

    def send(self, text: str) -> None:
        self.inner.send(text)
        self.store.log(self.chat_id, text, "out", self.clock())


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Telegram caps messages at 4096 chars; split on line boundaries."""
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                out.append(cur); cur = ""
            out.append(line[:limit]); line = line[limit:]
        if len(cur) + len(line) + 1 > limit and cur:
            out.append(cur); cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out or [""]
