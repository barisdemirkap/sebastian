"""Birthdays and anniversaries: from Google's Birthdays calendar (read-only) and from owner-added recurring
all-day events tagged 'birthday' (title contains the Turkish words for birthday). Pure functions; no I/O."""
from __future__ import annotations
import re
from datetime import date, datetime
from .models import Event
from . import i18n
from .textutil import fold

LEAD_DAYS = 3
def is_birthday(e: Event) -> bool:
    return e.tag == "birthday" or e.calendar == "birthdays" or "dogum gunu" in fold(e.title) or "birthday" in fold(e.title)


def person(e: Event) -> str:
    """Display name: the title without the birthday words and possessive suffixes (matched on the folded text,
    which has the same length as the title, so spans map back to the original spelling)."""
    f = fold(e.title)
    spans = [m.span() for m in re.finditer(r"dogum\s*gun\w*|birthday|yildonumu\w*|anniversary|['’]s\b|['’](?:nin|nun|in|un)\b", f)]
    name = "".join(c for i, c in enumerate(e.title) if not any(s <= i < e_ for s, e_ in spans))
    return re.sub(r"\s+", " ", name).strip(" -:,") or e.title


def _key(e: Event) -> tuple:
    return (re.sub(r"[^a-z0-9]", "", fold(person(e))), e.start.date())


def upcoming(events: list[Event], today: date, offsets: tuple[int, ...]) -> list[tuple[Event, int]]:
    """Birthday events whose date is exactly `offsets` days away, deduplicated across sources, sorted by date."""
    seen, out = set(), []
    for e in sorted((x for x in events if is_birthday(x)), key=lambda x: (x.start, x.title)):
        n = (e.start.date() - today).days
        if n in offsets and _key(e) not in seen:
            seen.add(_key(e))
            out.append((e, n))
    return out


def when_text(n: int) -> str:
    return i18n.t("when_today") if n == 0 else i18n.t("when_tomorrow") if n == 1 else i18n.t("when_days", n=n)


def section(events: list[Event], today: date, offsets: tuple[int, ...]) -> list[str]:
    items = upcoming(events, today, offsets)
    if not items:
        return []
    return [i18n.t("birthdays_head")] + [i18n.t("birthday_line", name=person(e), when=when_text(n)) for e, n in items]
