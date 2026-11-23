"""Sebastian's own Telegram reminders for calendar events, sent once (the sent-set is persisted by the scheduler).

Timed events: `reminder_minutes` (per-event override, e.g. the owner asks for one hour before) else the default lead.
All-day events: at `allday_at` (default 09:00) on the day. Birthdays are covered by the digests, not here."""
from __future__ import annotations
from datetime import datetime, time, timedelta
from .models import Event
from .merge import merge
from . import i18n


def reminder_key(e: Event) -> str:
    return f"{e.uid or e.title.lower()}|{e.start.isoformat()}"


def reminder_at(e: Event, lead: timedelta, allday_at: time) -> datetime:
    if e.all_day:
        return e.start.replace(hour=allday_at.hour, minute=allday_at.minute, second=0, microsecond=0)
    return e.start - (timedelta(minutes=e.reminder_minutes) if e.reminder_minutes is not None else lead)


def due_reminders(events: list[Event], now: datetime, lead: timedelta, already: set[str],
                  allday_at: time = time(9, 0)) -> list[Event]:
    """Reminder moment reached and the event (or its day, for all-day) not over yet. A restart inside that window
    still sends it once; the sent-set prevents repeats."""
    out = []
    for e in merge(events):
        if e.tag == "birthday" or reminder_key(e) in already:
            continue
        at = reminder_at(e, lead, allday_at)
        if e.all_day:
            if at <= now < e.end:
                out.append(e)
        elif at <= now < e.start:
            out.append(e)
    return out


def format_reminder(e: Event, now: datetime) -> str:
    if e.all_day:
        return i18n.t("reminder_allday", title=e.title)
    mins = max(1, round((e.start - now).total_seconds() / 60))
    loc = f" @ {e.location}" if e.location else ""
    return i18n.t("reminder_timed", title=e.title, left=i18n.fmt_left(mins), time=f"{e.start.astimezone(now.tzinfo):%H:%M}", loc=loc)
