from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Account:
    provider: str  # "google" | "outlook"
    address: str
    label: str
    scopes: tuple[str, ...] = ()
    enabled: bool = False
    mail_enabled: bool = False  # Phase 2 mail reading; separate owner approval, needs `enabled` too


@dataclass(frozen=True)
class Event:
    title: str
    start: datetime
    end: datetime
    account_label: str
    location: str = ""
    attendees: tuple[str, ...] = ()
    all_day: bool = False
    uid: str = ""
    flags: tuple[str, ...] = field(default=())
    event_id: str = ""            # provider event id (needed to edit/delete); uid is the iCal id used for dedupe
    series_id: str = ""           # recurring series master id, "" when not recurring
    notes: str = ""
    reminder_minutes: int | None = None  # per-event override for Sebastian's own Telegram reminder
    tag: str = ""                 # "birthday" for birthday/anniversary events
    calendar: str = "primary"     # "primary" or "birthdays" (Google's read-only Birthdays calendar)


@dataclass(frozen=True)
class EventDraft:
    """A calendar event the owner asked for, before it exists. Built by code from validated intent slots."""
    title: str
    start: datetime               # tz-aware; for all-day events the local midnight of the first day
    end: datetime                 # exclusive; for all-day events the local midnight after the last day
    all_day: bool = False
    location: str = ""
    notes: str = ""
    reminder_minutes: int | None = None
    recurrence: str = ""          # "", "daily", "weekly", "yearly"
    tag: str = ""
