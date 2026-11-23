import json
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sebastian.http import Response, UrllibTransport, WriteAccessDenied, LiveAccessDenied
from sebastian.models import Account, EventDraft
from sebastian.providers import GoogleCalendarProvider, FixtureProvider, WriteDisabled, parse_google_event, write_allowed

IST = ZoneInfo("Europe/Istanbul")
RW = ("https://www.googleapis.com/auth/calendar.readonly", "https://www.googleapis.com/auth/calendar.events")
ACCT = Account("google", "me@example.com", "personal", RW, True)


class Tok:
    def access_token(self):
        return "tok"


class Rec:
    def __init__(self, bodies):
        self.bodies, self.calls = list(bodies), []

    def request(self, method, url, headers=None, form=None, json_body=None):
        self.calls.append((method, url, json_body))
        return Response(200, self.bodies.pop(0) if self.bodies else {})


def prov(tr, allow=True, acct=ACCT, **kw):
    return GoogleCalendarProvider(acct, Tok(), tr, tz=IST, allow_write=lambda: allow, **kw)


def draft(**kw):
    s = datetime(2026, 11, 20, 15, 0, tzinfo=IST)
    return EventDraft(**{"title": "Dis hekimi", "start": s, "end": s + timedelta(hours=1), **kw})


def test_create_posts_timed_event_with_sebastian_props_and_no_google_popup():
    tr = Rec([{"id": "e1", "summary": "Dis hekimi", "start": {"dateTime": "2026-11-20T15:00:00+03:00"},
               "end": {"dateTime": "2026-11-20T16:00:00+03:00"}}])
    ev = prov(tr).create_event(draft(reminder_minutes=60, location="Kadikoy", notes="rontgen"))
    m, url, body = tr.calls[0]
    assert m == "POST" and url.endswith("/calendars/primary/events")
    assert body["summary"] == "Dis hekimi" and body["location"] == "Kadikoy" and body["description"] == "rontgen"
    assert body["start"] == {"dateTime": "2026-11-20T15:00:00+03:00", "timeZone": "Europe/Istanbul"}
    assert body["reminders"] == {"useDefault": False}
    assert body["extendedProperties"]["private"]["sebastianReminder"] == "60"
    assert ev.event_id == "e1"


def test_all_day_and_recurrence_body():
    tr = Rec([{"id": "b1", "summary": "Ayse dogum gunu", "start": {"date": "2027-03-03"}, "end": {"date": "2027-03-04"}}])
    d = EventDraft("Ayse dogum gunu", datetime(2027, 3, 3, tzinfo=IST), datetime(2027, 3, 4, tzinfo=IST),
                   all_day=True, recurrence="yearly", tag="birthday")
    prov(tr).create_event(d)
    body = tr.calls[0][2]
    assert body["start"] == {"date": "2027-03-03"} and body["end"] == {"date": "2027-03-04"}
    assert body["recurrence"] == ["RRULE:FREQ=YEARLY"]
    assert body["extendedProperties"]["private"]["sebastianTag"] == "birthday"
    for rec, rule in (("daily", "DAILY"), ("weekly", "WEEKLY")):
        tr = Rec([{"id": "x", "start": {"date": "2027-03-03"}, "end": {"date": "2027-03-04"}}])
        prov(tr).create_event(draft(recurrence=rec))
        assert tr.calls[0][2]["recurrence"] == [f"RRULE:FREQ={rule}"]


def test_update_patches_only_given_fields_and_delete_calls_delete():
    tr = Rec([{"id": "e1", "start": {"dateTime": "2026-11-21T10:00:00+03:00"}, "end": {"dateTime": "2026-11-21T11:00:00+03:00"}}, {}])
    p = prov(tr)
    p.update_event("e1", {"title": "Yeni", "start": datetime(2026, 11, 21, 10, tzinfo=IST), "end": datetime(2026, 11, 21, 11, tzinfo=IST)})
    m, url, body = tr.calls[0]
    assert m == "PATCH" and url.endswith("/events/e1")
    assert set(body) == {"summary", "start", "end"}
    p.delete_event("e1")
    assert tr.calls[1][0] == "DELETE" and tr.calls[1][1].endswith("/events/e1")


def test_event_id_is_quoted():
    tr = Rec([{}])
    prov(tr).delete_event("a/b?c")
    assert "a%2Fb%3Fc" in tr.calls[0][1]


def test_writes_blocked_without_flag_or_scope():
    tr = Rec([])
    with pytest.raises(WriteDisabled):
        prov(tr, allow=False).create_event(draft())
    with pytest.raises(WriteDisabled):
        prov(tr, acct=Account("google", "a", "p", RW[:1], True)).delete_event("e1")
    assert tr.calls == []


def test_write_allowed_needs_both_env_flags():
    assert not write_allowed({})
    assert not write_allowed({"SEBASTIAN_ALLOW_LIVE": "1"})
    assert not write_allowed({"SEBASTIAN_ALLOW_WRITE": "1"})
    assert write_allowed({"SEBASTIAN_ALLOW_LIVE": "1", "SEBASTIAN_ALLOW_WRITE": "1"})


def test_real_transport_refuses_calendar_writes_without_flag(monkeypatch):
    monkeypatch.setenv("SEBASTIAN_ALLOW_LIVE", "1")
    monkeypatch.delenv("SEBASTIAN_ALLOW_WRITE", raising=False)
    with pytest.raises(WriteAccessDenied):
        UrllibTransport().request("POST", "https://www.googleapis.com/calendar/v3/calendars/primary/events", json_body={})
    monkeypatch.delenv("SEBASTIAN_ALLOW_LIVE")
    with pytest.raises(LiveAccessDenied):
        UrllibTransport().request("GET", "https://www.googleapis.com/calendar/v3/x")


def test_parse_extended_props_series_and_local_all_day():
    e = parse_google_event({"id": "i1", "summary": "Cop", "recurringEventId": "s1", "description": "n",
                            "start": {"date": "2026-11-18"}, "end": {"date": "2026-11-19"},
                            "extendedProperties": {"private": {"sebastianReminder": "45", "sebastianTag": "birthday"}}}, "p", IST)
    assert (e.event_id, e.series_id, e.notes, e.reminder_minutes, e.tag) == ("i1", "s1", "n", 45, "birthday")
    assert e.start == datetime(2026, 11, 18, tzinfo=IST) and e.end == datetime(2026, 11, 19, tzinfo=IST)


def test_birthdays_calendar_read_is_separate_readonly_call():
    tr = Rec([{"items": []}, {"items": [{"id": "bd1", "summary": "Ali's birthday", "eventType": "birthday",
                                        "start": {"date": "2026-11-20"}, "end": {"date": "2026-11-21"}}]}])
    evs = prov(tr, birthdays=True).events(datetime(2026, 11, 18, tzinfo=IST), datetime(2026, 11, 23, tzinfo=IST))
    assert [c[0] for c in tr.calls] == ["GET", "GET"]
    assert "addressbook%23contacts%40group.v.calendar.google.com" in tr.calls[1][1]
    assert evs[0].tag == "birthday" and evs[0].calendar == "birthdays"


def test_birthday_failure_does_not_lose_primary_events():
    class Boom(Rec):
        def request(self, method, url, headers=None, form=None, json_body=None):
            if "addressbook" in url:
                raise RuntimeError("404")
            return super().request(method, url, headers, form, json_body)
    tr = Boom([{"items": [{"id": "1", "summary": "A", "start": {"dateTime": "2026-11-18T10:00:00+03:00"},
                           "end": {"dateTime": "2026-11-18T11:00:00+03:00"}}]}])
    assert len(prov(tr, birthdays=True).events(datetime(2026, 11, 18, tzinfo=IST), datetime(2026, 11, 19, tzinfo=IST))) == 1


def test_fixture_provider_supports_the_same_write_interface():
    f = FixtureProvider([])
    e = f.create_event(draft())
    assert f.events(datetime(2026, 11, 20, tzinfo=IST), datetime(2026, 11, 21, tzinfo=IST))[0].event_id == e.event_id
    f.update_event(e.event_id, {"title": "Baska"})
    assert f.events(datetime(2026, 11, 20, tzinfo=IST), datetime(2026, 11, 21, tzinfo=IST))[0].title == "Baska"
    f.delete_event(e.event_id)
    assert f.events(datetime(2026, 11, 20, tzinfo=IST), datetime(2026, 11, 21, tzinfo=IST)) == []
