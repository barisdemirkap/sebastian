"""Pure functions over mail metadata: unanswered threads and meeting prep. No I/O, no sending."""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from .mail import MailMessage
from .models import Event

AUTOMATED = re.compile(r"(no-?reply|do-?not-?reply|notifications?|mailer-daemon|newsletter|bounce)", re.I)
STOP = {"meeting", "sync", "call", "weekly", "standup", "review", "with", "from", "this", "that", "catch", "chat"}


@dataclass(frozen=True)
class NeedsReply:
    account_label: str
    thread_id: str
    sender: str
    subject: str
    age: timedelta
    priority: bool = False


@dataclass(frozen=True)
class PrepNote:
    event_title: str
    threads: tuple[tuple[str, str, bool], ...]  # (subject, counterpart address, waiting_on_owner)


def _threads(messages: list[MailMessage]) -> dict[tuple[str, str], list[MailMessage]]:
    out: dict[tuple[str, str], list[MailMessage]] = {}
    for m in sorted(messages, key=lambda x: x.date):
        out.setdefault((m.account_label, m.thread_id), []).append(m)
    return out


def unanswered_threads(messages: list[MailMessage], now: datetime, min_age: timedelta = timedelta(hours=4),
                       max_age: timedelta = timedelta(days=7), priority_senders: tuple[str, ...] = (),
                       limit: int = 10) -> list[NeedsReply]:
    """Threads whose last message is from someone else, old enough to matter, not automated."""
    prio = {p.lower() for p in priority_senders}
    out = []
    for (label, tid), msgs in _threads(messages).items():
        last = msgs[-1]
        age = now - last.date
        if last.from_owner or AUTOMATED.search(last.sender) or not (min_age <= age <= max_age):
            continue
        out.append(NeedsReply(label, tid, last.sender, last.subject, age, last.sender in prio))
    out.sort(key=lambda n: (not n.priority, -n.age.total_seconds()))
    return out[:limit]


def _keywords(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) >= 4 and w not in STOP}


def meeting_prep(event: Event, messages: list[MailMessage], now: datetime, owners: tuple[str, ...] = (),
                 lookback: timedelta = timedelta(days=14), limit: int = 3) -> PrepNote | None:
    """Recent threads involving the event's attendees or sharing a title keyword."""
    own = {o.lower() for o in owners}
    people = {a.lower() for a in event.attendees} - own
    words = _keywords(event.title)
    hits = []
    for (_, _tid), msgs in _threads(messages).items():
        recent = [m for m in msgs if now - m.date <= lookback]
        if not recent:
            continue
        involved = {m.sender for m in recent} | {r for m in recent for r in m.recipients}
        last = recent[-1]
        if (people & involved) or (words and words & _keywords(last.subject)):
            counterpart = next(iter(sorted((people & involved) or {last.sender})), last.sender)
            hits.append((last.date, last.subject, counterpart, not last.from_owner and not AUTOMATED.search(last.sender)))
    if not hits:
        return None
    hits.sort(reverse=True)
    return PrepNote(event.title, tuple((s, c, w) for _, s, c, w in hits[:limit]))


def _age(a: timedelta) -> str:
    h = int(a.total_seconds() // 3600)
    return f"{h // 24}d" if h >= 48 else f"{h}h"


def format_needs_reply(items: list[NeedsReply]) -> list[str]:
    if not items:
        return []
    lines = ["Needs reply:"]
    for label in sorted({i.account_label for i in items}):
        lines.append(f"  [{label}]")
        lines += [f"  - {'(priority) ' if i.priority else ''}{i.sender}: {i.subject} ({_age(i.age)})" for i in items if i.account_label == label]
    return lines


def format_prep(notes: list[PrepNote]) -> list[str]:
    if not notes:
        return []
    lines = ["Meeting prep:"]
    for n in notes:
        lines.append(f"  {n.event_title}")
        lines += [f"  - {s} ({c}){' - waiting on you' if w else ''}" for s, c, w in n.threads]
    return lines
