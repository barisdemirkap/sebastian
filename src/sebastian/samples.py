"""Fixture events for `sebastian demo` and docs. Fictional data."""
from __future__ import annotations
from datetime import datetime, timedelta
from .models import Event


def sample_events(now: datetime) -> list[Event]:
    d = now.replace(hour=0, minute=0, second=0, microsecond=0)
    def e(day, h, m, dur, title, label, loc="", uid=""):
        s = d + timedelta(days=day, hours=h, minutes=m)
        return Event(title, s, s + timedelta(minutes=dur), label, loc, uid=uid)
    return [
        e(0, 9, 0, 60, "Standup", "personal", "Meet", "u-standup"),
        e(0, 9, 0, 60, "Standup", "company", "Meet", "u-standup"),
        e(0, 9, 30, 60, "Dentist", "personal"),
        e(0, 14, 0, 30, "1:1", "company", "Room 4"),
        e(0, 14, 35, 30, "Review", "company"),
        e(1, 10, 0, 45, "Planning", "company", "Room 2"),
    ]


def sample_mail(now: datetime):
    """Fictional mail metadata for `sebastian demo`."""
    from .mail import MailMessage
    def m(label, tid, sender, subject, hours_ago, mine=False, to=("me@example.com",)):
        return MailMessage(label, tid, f"{tid}-{hours_ago}", sender, to, subject, now - timedelta(hours=hours_ago), mine)
    return [
        m("personal", "t1", "dana@example.org", "Contract draft v2", 30),
        m("company", "t2", "boss@example.com", "Q4 planning inputs", 9),
        m("company", "t3", "noreply@tool.example", "Your weekly report", 5),
        m("company", "t4", "me@example.com", "Re: Room 2 agenda", 3, True, ("pat@example.com",)),
    ]
