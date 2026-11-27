from __future__ import annotations
from datetime import datetime, timedelta, tzinfo
from .models import Event
from .merge import merge
from .insights import unanswered_threads, meeting_prep, format_needs_reply, format_prep
from .mail import MailMessage
from . import birthdays, i18n
from .textutil import fold


def day_events(events: list[Event], day: datetime) -> list[Event]:
    """Merged events overlapping the local day of `day`; birthdays are shown in their own section."""
    s = day.replace(hour=0, minute=0, second=0, microsecond=0)
    e = s + timedelta(days=1)
    return [x for x in merge(events) if x.start < e and x.end > s and not birthdays.is_birthday(x)]


_day_events = day_events


def _line(e: Event, tz: tzinfo) -> str:
    when = i18n.t("all_day") if e.all_day else f"{e.start.astimezone(tz):%H:%M}-{e.end.astimezone(tz):%H:%M}"
    flag = f" [{', '.join(i18n.FLAGS.get(f, f) for f in e.flags)}]" if e.flags else ""
    loc = f" @ {e.location}" if e.location else ""
    return f"- {when} {e.title} ({e.account_label}){loc}{flag}"


def _mail_sections(messages, now, owners, events=()) -> list[str]:
    """Optional Phase 2 sections; empty when no mail data is available."""
    if not messages:
        return []
    notes = [n for e in events if not e.all_day and (n := meeting_prep(e, messages, now, owners))]
    return format_prep(notes) + format_needs_reply(unanswered_threads(messages, now))


def _task_section(tasks: list[str] | None) -> list[str]:
    return [i18n.t("tasks_head")] + [i18n.t("task_line", x=x) for x in tasks[:15]] if tasks else []


SHOPPING = ("alisveris", "market")      # a calendar title containing one of these (folded) makes it a shopping day


def _list_sections(lists: list[tuple[str, list[str]]] | None, today: list[Event]) -> list[str]:
    """Named lists (ADR 0016) appear in the morning digest only on a day the calendar has a shopping event."""
    if not lists or not any(w in fold(e.title) for e in today for w in SHOPPING):
        return []
    out: list[str] = []
    for name, items in lists:
        if items:
            out += [i18n.t("lst_digest_head", list=name)] + [i18n.t("lst_digest_row", x=x) for x in items[:30]]
    return out


def morning_digest(events: list[Event], now: datetime, messages: list[MailMessage] | None = None,
                   owners: tuple[str, ...] = (), tasks: list[str] | None = None,
                   lists: list[tuple[str, list[str]]] | None = None, calendar: bool = True) -> str:
    """`calendar=False` (plugin off, ADR 0005): no events, birthdays or shopping-day lists; the rest still shows."""
    today = day_events(events, now) if calendar else []
    if not calendar:
        lines = []
    elif not today:
        lines = [i18n.t("morning_empty")]
    else:
        lines = [i18n.t("morning_head", date=i18n.fmt_date(now, True), n=len(today))] + [_line(e, now.tzinfo) for e in today]
        n = sum("conflict" in e.flags for e in today)
        if n:
            lines.append(i18n.t("conflict_warn", n=n))
    lines += birthdays.section(events, now.date(), (0, birthdays.LEAD_DAYS)) if calendar else []
    lines += _task_section(tasks)
    lines += _list_sections(lists, today)
    lines = lines + _mail_sections(messages, now, owners, today)
    return "\n".join(lines + ([i18n.t("digest_close_morning")] if today else []))


def evening_digest(events: list[Event], now: datetime, messages: list[MailMessage] | None = None,
                   owners: tuple[str, ...] = (), tasks: list[str] | None = None, calendar: bool = True) -> str:
    tomorrow = now + timedelta(days=1)
    items = day_events(events, tomorrow) if calendar else []
    missed = [e for e in day_events(events, now) if e.end <= now and not e.all_day] if calendar else []
    if not calendar:
        lines = []
    elif items:
        lines = [i18n.t("evening_head", date=i18n.fmt_date(tomorrow, True), n=len(items))] + [_line(e, now.tzinfo) for e in items]
    else:
        lines = [i18n.t("evening_empty")]
    if calendar:
        lines.append(i18n.t("recap", n=len(missed)))
        lines += birthdays.section(events, now.date(), (1,))
    lines += _task_section(tasks)
    lines = lines + _mail_sections(messages, now, owners)
    return "\n".join(lines + ([i18n.t("digest_close_evening")] if items else []))


def plan_digest(events: list[Event], now: datetime) -> str:
    """22:00 'tomorrow morning plan': tomorrow's all-day items and everything before noon."""
    tomorrow = now + timedelta(days=1)
    noon = tomorrow.replace(hour=12, minute=0, second=0, microsecond=0)
    items = [e for e in day_events(events, tomorrow) if e.all_day or e.start < noon]
    bd = birthdays.section(events, tomorrow.date(), (0,))
    if not items and not bd:
        return i18n.t("plan_empty")
    lines = [i18n.t("plan_head", date=i18n.fmt_date(tomorrow, True))]
    timed = [e for e in items if not e.all_day]
    if timed:
        lines.append(i18n.t("plan_first", time=f"{timed[0].start.astimezone(now.tzinfo):%H:%M}", title=timed[0].title))
    lines += [_line(e, now.tzinfo) for e in items]
    return "\n".join(lines + bd)
