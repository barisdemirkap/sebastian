from datetime import datetime, timedelta, timezone
from sebastian.models import Event
from sebastian.merge import merge
from sebastian.digest import morning_digest, evening_digest
from sebastian.providers import parse_google_event, parse_outlook_event, FixtureProvider

T = timezone.utc
def ev(title, h, m, dur, label="personal", uid=""):
    s = datetime(2026, 11, 18, h, m, tzinfo=T)
    return Event(title, s, s + timedelta(minutes=dur), label, uid=uid)

def test_dedupe_across_accounts():
    out = merge([ev("Sync", 10, 0, 30, "personal", "u1"), ev("Sync", 10, 0, 30, "company", "u1")])
    assert len(out) == 1

def test_conflict_and_back_to_back():
    out = merge([ev("A", 9, 0, 60), ev("B", 9, 30, 60), ev("C", 10, 35, 30)])
    f = {e.title: e.flags for e in out}
    assert "conflict" in f["A"] and "conflict" in f["B"]
    assert "back-to-back" in f["B"] or "back-to-back" in f["C"]

def test_morning_digest():
    now = datetime(2026, 11, 18, 8, 30, tzinfo=T)
    s = morning_digest([ev("A", 9, 0, 60), ev("B", 9, 30, 60)], now)
    assert "2 kayıt" in s and "çakışıyor" in s

def test_evening_digest_empty():
    now = datetime(2026, 11, 18, 20, 0, tzinfo=T)
    assert "Yarın takvimde bir şey yok" in evening_digest([], now)

def test_parsers():
    g = parse_google_event({"summary": "X", "start": {"dateTime": "2026-11-18T09:00:00+03:00"}, "end": {"dateTime": "2026-11-18T10:00:00+03:00"}, "attendees": [{"email": "a@b.c"}]}, "personal")
    assert g.title == "X" and g.attendees == ("a@b.c",)
    o = parse_outlook_event({"subject": "Y", "start": {"dateTime": "2026-11-18T09:00:00", "timeZone": "UTC"}, "end": {"dateTime": "2026-11-18T10:00:00", "timeZone": "UTC"}, "attendees": []}, "company")
    assert o.title == "Y" and o.start.tzinfo is not None

def test_fixture_provider_range():
    p = FixtureProvider([ev("A", 9, 0, 60)])
    assert p.events(datetime(2026, 11, 18, tzinfo=T), datetime(2026, 11, 19, tzinfo=T))
