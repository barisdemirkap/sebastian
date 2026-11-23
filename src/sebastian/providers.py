from __future__ import annotations
import os
from dataclasses import replace
from datetime import datetime, date, timedelta, timezone, tzinfo
from typing import Callable, Protocol
from urllib.parse import urlencode, quote
from .http import Transport
from .models import Account, Event, EventDraft

CAL_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
BIRTHDAYS_CALENDAR = "addressbook#contacts@group.v.calendar.google.com"
RRULES = {"daily": "RRULE:FREQ=DAILY", "weekly": "RRULE:FREQ=WEEKLY", "yearly": "RRULE:FREQ=YEARLY"}


class CalendarProvider(Protocol):
    def events(self, start: datetime, end: datetime) -> list[Event]: ...


class CalendarWriter(Protocol):
    """Write side (ADR 0002). Only ever called by code that executes a stored, owner-confirmed pending action."""
    def create_event(self, draft: EventDraft) -> Event: ...
    def update_event(self, event_id: str, changes: dict) -> Event: ...
    def delete_event(self, event_id: str) -> None: ...


class WriteDisabled(RuntimeError):
    pass


def write_allowed(env=None) -> bool:
    env = os.environ if env is None else env
    return env.get("SEBASTIAN_ALLOW_LIVE") == "1" and env.get("SEBASTIAN_ALLOW_WRITE") == "1"


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def parse_google_event(item: dict, label: str, tz: tzinfo = timezone.utc, calendar: str = "primary") -> Event:
    s, e = item["start"], item["end"]
    all_day = "date" in s
    if all_day:  # all-day events are dates: anchor them at midnight of the owner's local timezone
        start = datetime.combine(date.fromisoformat(s["date"]), datetime.min.time(), tzinfo=tz)
        end = datetime.combine(date.fromisoformat(e["date"]), datetime.min.time(), tzinfo=tz)
    else:
        start, end = _dt(s["dateTime"]), _dt(e["dateTime"])
    priv = (item.get("extendedProperties") or {}).get("private") or {}
    try:
        rem = int(priv["sebastianReminder"]) if "sebastianReminder" in priv else None
    except ValueError:
        rem = None
    tag = priv.get("sebastianTag", "")
    if calendar == "birthdays" or item.get("eventType") == "birthday":
        tag = "birthday"
    return Event(item.get("summary", "(no title)"), start, end, label, item.get("location", ""),
                 tuple(a["email"] for a in item.get("attendees", []) if "email" in a), all_day,
                 item.get("iCalUId", item.get("id", "")), event_id=item.get("id", ""),
                 series_id=item.get("recurringEventId", ""), notes=item.get("description", ""),
                 reminder_minutes=rem, tag=tag, calendar=calendar)


def parse_outlook_event(item: dict, label: str) -> Event:
    start = _dt(item["start"]["dateTime"] + ("Z" if item["start"].get("timeZone") == "UTC" and not item["start"]["dateTime"].endswith("Z") else ""))
    end = _dt(item["end"]["dateTime"] + ("Z" if item["end"].get("timeZone") == "UTC" and not item["end"]["dateTime"].endswith("Z") else ""))
    return Event(item.get("subject", "(no title)"), start, end, label, (item.get("location") or {}).get("displayName", ""),
                 tuple(a["emailAddress"]["address"] for a in item.get("attendees", [])), bool(item.get("isAllDay")), item.get("iCalUId", item.get("id", "")))


class _Token(Protocol):
    def access_token(self) -> str: ...


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _when(d: datetime, all_day: bool, tz: tzinfo) -> dict:
    d = d.astimezone(tz)
    if all_day:
        return {"date": d.date().isoformat()}
    return {"dateTime": d.isoformat(), "timeZone": getattr(tz, "key", "UTC")}


def google_body(draft: EventDraft, tz: tzinfo) -> dict:
    priv = {}
    if draft.reminder_minutes is not None:
        priv["sebastianReminder"] = str(draft.reminder_minutes)
    if draft.tag:
        priv["sebastianTag"] = draft.tag
    body = {"summary": draft.title, "start": _when(draft.start, draft.all_day, tz), "end": _when(draft.end, draft.all_day, tz),
            "reminders": {"useDefault": False}}  # Sebastian sends its own reminders; no duplicate Google popup
    if draft.location:
        body["location"] = draft.location
    if draft.notes:
        body["description"] = draft.notes
    if draft.recurrence:
        body["recurrence"] = [RRULES[draft.recurrence]]
    if priv:
        body["extendedProperties"] = {"private": priv}
    return body


def google_patch(changes: dict, tz: tzinfo) -> dict:
    """Only the fields present in `changes` (title, start+end, all_day, location, notes, reminder_minutes)."""
    body: dict = {}
    if "title" in changes:
        body["summary"] = changes["title"]
    if "start" in changes and "end" in changes:
        ad = bool(changes.get("all_day", False))
        body["start"], body["end"] = _when(changes["start"], ad, tz), _when(changes["end"], ad, tz)
    if "location" in changes:
        body["location"] = changes["location"]
    if "notes" in changes:
        body["description"] = changes["notes"]
    if "reminder_minutes" in changes:
        body["extendedProperties"] = {"private": {"sebastianReminder": str(changes["reminder_minutes"])}}
    return body


class GoogleCalendarProvider:
    """Google Calendar. Reads need calendar.readonly; writes need calendar.events in the account scopes AND
    SEBASTIAN_ALLOW_LIVE=1 + SEBASTIAN_ALLOW_WRITE=1 (ADR 0002). Network goes through the injected Transport."""
    BASE = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    BIRTHDAY_BASE = "https://www.googleapis.com/calendar/v3/calendars/" + quote(BIRTHDAYS_CALENDAR, safe="") + "/events"

    def __init__(self, account: Account, tokens: _Token, transport: Transport, max_pages: int = 20,
                 tz: tzinfo = timezone.utc, allow_write: Callable[[], bool] = write_allowed, birthdays: bool = False):
        self.account, self.tokens, self.transport, self.max_pages = account, tokens, transport, max_pages
        self.tz, self.allow_write, self.birthdays = tz, allow_write, birthdays

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.tokens.access_token()}"}

    def _list(self, base: str, start: datetime, end: datetime, calendar: str) -> list[Event]:
        out: list[Event] = []
        page = ""
        for _ in range(self.max_pages):
            q = {"timeMin": _iso(start), "timeMax": _iso(end), "singleEvents": "true", "orderBy": "startTime", "maxResults": "250"}
            if page:
                q["pageToken"] = page
            r = self.transport.request("GET", f"{base}?{urlencode(q)}", self._headers())
            out += [parse_google_event(i, self.account.label, self.tz, calendar) for i in r.body.get("items", [])
                    if i.get("status") != "cancelled"]
            page = r.body.get("nextPageToken", "")
            if not page:
                break
        return out

    def events(self, start: datetime, end: datetime) -> list[Event]:
        out = self._list(self.BASE, start, end, "primary")
        if self.birthdays:  # read-only; a missing/denied Birthdays calendar must not hide normal events
            try:
                out += self._list(self.BIRTHDAY_BASE, start, end, "birthdays")
            except Exception:  # noqa: BLE001
                pass
        return out

    def _check_write(self) -> None:
        if CAL_EVENTS_SCOPE not in self.account.scopes:
            raise WriteDisabled("account has no calendar.events scope")
        if not self.allow_write():
            raise WriteDisabled("writes need SEBASTIAN_ALLOW_LIVE=1 and SEBASTIAN_ALLOW_WRITE=1")

    def _one(self, event_id: str) -> str:
        return f"{self.BASE}/{quote(event_id, safe='')}"

    def create_event(self, draft: EventDraft) -> Event:
        self._check_write()
        r = self.transport.request("POST", self.BASE, self._headers(), json_body=google_body(draft, self.tz))
        return parse_google_event(r.body, self.account.label, self.tz)

    def update_event(self, event_id: str, changes: dict) -> Event:
        self._check_write()
        r = self.transport.request("PATCH", self._one(event_id), self._headers(), json_body=google_patch(changes, self.tz))
        return parse_google_event(r.body, self.account.label, self.tz)

    def delete_event(self, event_id: str) -> None:
        self._check_write()
        self.transport.request("DELETE", self._one(event_id), self._headers())


class OutlookCalendarProvider:
    """Read-only Microsoft Graph calendarView. Requests UTC so parsing is unambiguous."""
    BASE = "https://graph.microsoft.com/v1.0/me/calendarView"

    def __init__(self, account: Account, tokens: _Token, transport: Transport, max_pages: int = 20):
        self.account, self.tokens, self.transport, self.max_pages = account, tokens, transport, max_pages

    def events(self, start: datetime, end: datetime) -> list[Event]:
        out: list[Event] = []
        url = f"{self.BASE}?{urlencode({'startDateTime': _iso(start), 'endDateTime': _iso(end), '$top': '100'})}"
        for _ in range(self.max_pages):
            r = self.transport.request("GET", url, {"Authorization": f"Bearer {self.tokens.access_token()}",
                                                    "Prefer": 'outlook.timezone="UTC"'})
            out += [parse_outlook_event(i, self.account.label) for i in r.body.get("value", []) if not i.get("isCancelled")]
            url = r.body.get("@odata.nextLink", "")
            if not url:
                break
        return out


class FixtureProvider:
    """In-memory calendar for demos and tests; also implements the write interface (no network)."""

    def __init__(self, events: list[Event], label: str = "personal"):
        self._events = list(events)
        self.label, self._n = label, 0

    def events(self, start: datetime, end: datetime) -> list[Event]:
        return [e for e in self._events if e.start < end and e.end > start]

    def create_event(self, draft: EventDraft) -> Event:
        self._n += 1
        e = Event(draft.title, draft.start, draft.end, self.label, draft.location, (), draft.all_day,
                  f"fx-{self._n}", event_id=f"fx-{self._n}", notes=draft.notes, reminder_minutes=draft.reminder_minutes,
                  tag=draft.tag, series_id=f"fx-{self._n}" if draft.recurrence else "")
        self._events.append(e)
        return e

    def update_event(self, event_id: str, changes: dict) -> Event:
        for i, e in enumerate(self._events):
            if e.event_id == event_id:
                kw = {}
                for k_, f_ in (("title", "title"), ("start", "start"), ("end", "end"), ("all_day", "all_day"),
                               ("location", "location"), ("notes", "notes"), ("reminder_minutes", "reminder_minutes")):
                    if k_ in changes:
                        kw[f_] = changes[k_]
                self._events[i] = replace(e, **kw)
                return self._events[i]
        raise KeyError(event_id)

    def delete_event(self, event_id: str) -> None:
        self._events = [e for e in self._events if e.event_id != event_id and e.series_id != event_id]
