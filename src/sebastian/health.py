"""Conversation health monitor (ADR 0009, ported in spirit from Acme). Pure signal logic plus a tiny SQLite store
(`health_events`, created by `store.SCHEMA`).

Signals (names only, never message content):
  S1 the bot was about to send the same text twice in a row (the repeat guard reworded it)
  S2 three or more consecutive unclear / unrecognised replies
  S3 a pending preview expired or could not be verified (it was not closed by the owner)
  S4 the owner showed frustration (words in `i18n.FRUSTRATION`)
  S5 the language-model chain failed or broke the schema
  S6 handling one update took more than SLOW_MS
  REPORT the owner flagged a problem with /bildir"""
from __future__ import annotations
import re
from collections import Counter
from datetime import datetime, timedelta

from . import i18n
from .textutil import fold

SIGNALS = ("S1", "S2", "S3", "S4", "S5", "S6", "REPORT")
UNCLEAR_RUN = 3                    # consecutive unclear replies that make S2
WINDOW = timedelta(minutes=10)     # two or more signals inside this window make an incident
COOLDOWN = timedelta(minutes=30)   # at most one way-out menu per this long
REPORT_GAP = timedelta(minutes=10)
SLOW_MS = 8000
RETENTION_DAYS = 30
UNCLEAR_KEYS = ("nl_unsure", "unclear_examples", "pending_unclear", "pick_again", "unknown_command", "gave_up")


def _prefix(key: str) -> str:
    return i18n.TR[key].split("{")[0][:40]


def is_unclear(reply: str) -> bool:
    """True when `reply` is one of the 'I did not understand' texts (matched by their fixed opening, repeat lead removed)."""
    for lead in i18n.REPEAT_LEADS:
        if reply.startswith(lead):
            reply = reply[len(lead):]
            break
    return any(reply.startswith(p) for p in (_prefix(k) for k in UNCLEAR_KEYS) if p)


def is_frustrated(text: str) -> bool:
    f = " " + " ".join(re.sub(r"[^a-z0-9]+", " ", fold(text)).split()) + " "     # whole words only ('bulan' is not 'ulan')
    return any(f" {p} " in f for p in i18n.FRUSTRATION)


def record(conn, chat_id: int, signal: str, detail: str, at: datetime) -> None:
    conn.execute("INSERT INTO health_events(chat_id, at, signal, detail) VALUES (?,?,?,?)",
                 (chat_id, at.isoformat(timespec="seconds"), signal, detail[:80]))
    conn.commit()


def signals_since(conn, chat_id: int, since: datetime) -> list[str]:
    rows = conn.execute("SELECT signal FROM health_events WHERE chat_id=? AND at>=? ORDER BY id",
                        (chat_id, since.isoformat(timespec="seconds"))).fetchall()
    return [r[0] for r in rows]


def counts(conn, since: datetime) -> dict[int, Counter]:
    """Signal counts per chat since `since`; no message text exists in the table."""
    out: dict[int, Counter] = {}
    for r in conn.execute("SELECT chat_id, signal FROM health_events WHERE at>=? ORDER BY id",
                          (since.isoformat(timespec="seconds"),)):
        out.setdefault(r[0], Counter())[r[1]] += 1
    return out


def is_incident(recent: list[str], unclear_run: int, frustrated: bool) -> bool:
    """S2 (three unclear replies in a row), S4 (frustration) or two or more signals inside WINDOW."""
    return frustrated or unclear_run >= UNCLEAR_RUN or len(recent) >= 2


def purge(conn, now: datetime, days: int = RETENTION_DAYS) -> int:
    cut = (now - timedelta(days=days)).isoformat(timespec="seconds")
    n = conn.execute("DELETE FROM health_events WHERE at < ?", (cut,)).rowcount
    conn.commit()
    return n


def format_counts(c: Counter) -> str:
    return ", ".join(f"{s}={c[s]}" for s in SIGNALS if c.get(s))
