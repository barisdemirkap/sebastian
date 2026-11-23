"""WhatsApp Business adapter interface (Phase 3), offline only.

Design (see docs/phase3-design.md): the official Cloud API delivers inbound messages by webhook, so the
reader is fed by a webhook parser, not by polling. The interface has NO send capability; any send path
needs a new ADR superseding ADR 0001. No live API, no number registration, no network code lives here.
Only metadata is kept: message text is dropped at parse time."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

WINDOW = timedelta(hours=24)  # WhatsApp customer-service window; replies after this need templates (a send concern)


@dataclass(frozen=True)
class WaMessage:
    chat_id: str          # counterpart's WhatsApp id (phone number); never logged unmasked
    message_id: str
    timestamp: datetime
    inbound: bool         # True: customer -> business number
    kind: str = "text"    # type only, never the content
    name: str = ""


@dataclass(frozen=True)
class WaNeedsReply:
    chat: str             # masked
    name: str
    age: timedelta
    window_open: bool     # still inside the 24h service window


class WhatsAppReader(Protocol):
    """Read-only. Intentionally has no send/reply method."""
    def messages(self, since: datetime, until: datetime) -> list[WaMessage]: ...


def mask(chat_id: str) -> str:
    return "*" * len(chat_id) if len(chat_id) <= 6 else "*" * (len(chat_id) - 3) + chat_id[-3:]


def parse_webhook(payload: dict) -> list[WaMessage]:
    """Parse a Cloud API webhook body (entry[].changes[].value.messages[]). Text bodies are discarded."""
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            v = change.get("value", {})
            names = {c.get("wa_id", ""): c.get("profile", {}).get("name", "") for c in v.get("contacts", [])}
            for m in v.get("messages", []):
                ts = datetime.fromtimestamp(int(m["timestamp"]), tz=timezone.utc)
                out.append(WaMessage(m["from"], m["id"], ts, True, m.get("type", "text"), names.get(m["from"], "")))
    return out


class FixtureWhatsAppReader:
    def __init__(self, messages: list[WaMessage]):
        self._m = messages

    def messages(self, since: datetime, until: datetime) -> list[WaMessage]:
        return [m for m in self._m if since <= m.timestamp < until]


def unanswered_chats(messages: list[WaMessage], now: datetime, min_age: timedelta = timedelta(hours=1),
                     limit: int = 10) -> list[WaNeedsReply]:
    """Chats whose last message is inbound and at least `min_age` old (oldest first)."""
    last: dict[str, WaMessage] = {}
    for m in sorted(messages, key=lambda x: x.timestamp):
        last[m.chat_id] = m
    out = [WaNeedsReply(mask(m.chat_id), m.name, now - m.timestamp, now - m.timestamp < WINDOW)
           for m in last.values() if m.inbound and now - m.timestamp >= min_age]
    return sorted(out, key=lambda n: -n.age.total_seconds())[:limit]


def format_wa_needs_reply(items: list[WaNeedsReply]) -> list[str]:
    if not items:
        return []
    return ["WhatsApp Business needs reply:"] + [
        f"  - {i.name or i.chat} ({int(i.age.total_seconds() // 3600)}h){'' if i.window_open else ' [window closed]'}" for i in items]
