from dataclasses import replace
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from sebastian import birthdays, i18n
from sebastian.digest import morning_digest, evening_digest, plan_digest
from sebastian.models import Event
from sebastian.notify import StubNotifier
from sebastian.reminders import due_reminders, format_reminder, reminder_key
from sebastian.scheduler import Scheduler, State

IST = ZoneInfo("Europe/Istanbul")


def at(day, h=0, m=0):
    return datetime(2026, 11, day, h, m, tzinfo=IST)


def timed(title, day, h, m=0, dur=60, **kw):
    return Event(title, at(day, h, m), at(day, h, m) + timedelta(minutes=dur), "personal", uid=title, **kw)


def allday(title, day, **kw):
    return Event(title, at(day), at(day + 1), "personal", all_day=True, uid=title + str(day), **kw)


def test_default_lead_is_30_and_catch_up_inside_window():
    e = timed("Dis hekimi", 18, 15, 0)
    d = timedelta(minutes=30)
    assert due_reminders([e], at(18, 14, 29), d, set()) == []
    assert due_reminders([e], at(18, 14, 30), d, set()) == [e]
    assert due_reminders([e], at(18, 14, 55), d, set()) == [e]       # restart late: still sent once
    assert due_reminders([e], at(18, 15, 0), d, set()) == []          # started: too late
    assert due_reminders([e], at(18, 14, 40), d, {reminder_key(e)}) == []


def test_per_event_override_one_hour():
    e = timed("Toplanti", 18, 15, 0, reminder_minutes=60)
    d = timedelta(minutes=30)
    assert due_reminders([e], at(18, 14, 0), d, set()) == [e]
    assert due_reminders([replace(e, reminder_minutes=None)], at(18, 14, 0), d, set()) == []


def test_all_day_reminder_at_nine_and_birthdays_excluded():
    a, b = allday("Fatura son gun", 18), allday("Ali dogum gunu", 18, tag="birthday")
    d = timedelta(minutes=30)
    assert due_reminders([a, b], at(18, 8, 59), d, set()) == []
    assert due_reminders([a, b], at(18, 9, 0), d, set()) == [a]
    assert due_reminders([a, b], at(19, 0, 1), d, set()) == []


def test_reminder_text_turkish():
    assert format_reminder(timed("Dis", 18, 15, 0), at(18, 14, 30)) == "Hatırlatma: Dis 30 dk sonra, 15:00"
    assert format_reminder(timed("Dis", 18, 15, 0, location="Kadikoy"), at(18, 14, 0)).startswith("Hatırlatma: Dis 1 saat sonra, 15:00 @ Kadikoy")
    assert format_reminder(allday("Fatura", 18), at(18, 9)) == "Hatırlatma: bugün Fatura (tüm gün)"


def test_birthdays_three_days_before_and_on_the_day_deduped():
    google = allday("Ayse's birthday", 21, calendar="birthdays", tag="birthday")
    own = allday("Ayşe'nin doğum günü", 21, tag="birthday")
    today = allday("Ali doğum günü", 18, tag="birthday")
    far = allday("Veli doğum günü", 25, tag="birthday")
    evs = [google, own, today, far]
    assert [(birthdays.person(e), n) for e, n in birthdays.upcoming(evs, at(18).date(), (0, 3))] == [("Ali", 0), ("Ayse", 3)]
    m = morning_digest(evs, at(18, 8, 30))
    assert "Doğum günleri:" in m and "- Ali (bugün)" in m and "(3 gün sonra)" in m and "Veli" not in m
    assert "tüm gün" not in m  # not repeated as a normal item
    e = evening_digest([allday("Ali doğum günü", 19, tag="birthday")], at(18, 20))
    assert "- Ali (yarın)" in e


def test_title_with_dogum_gunu_counts_without_tag():
    assert birthdays.is_birthday(allday("Anne Doğum Günü", 20))
    assert birthdays.person(allday("Anne Doğum Günü", 20)) == "Anne"


def test_plan_digest_morning_only_and_empty():
    evs = [timed("Kahvalti", 19, 9, 0), timed("Sinema", 19, 21, 0)]
    p = plan_digest(evs, at(18, 22))
    assert p.startswith("Yarın sabah planınız (Per 19 Kas):") and "İlk iş: 09:00 Kahvalti" in p and "Sinema" not in p
    assert plan_digest([], at(18, 22)) == "Yarın sabah için kayıt yok."


def test_scheduler_sends_plan_at_22_once_and_persists(tmp_path):
    evs = [timed("Kahvalti", 19, 9, 0)]
    n = StubNotifier()
    s = Scheduler(time(8, 30), time(19, 0), lambda now: evs, n, State(tmp_path / "s.json"), plan=time(22, 0))
    assert s.tick(at(18, 21, 59)) == []
    assert len(s.tick(at(18, 22, 0))) == 1
    s2 = Scheduler(time(8, 30), time(19, 0), lambda now: evs, StubNotifier(), State(tmp_path / "s.json"), plan=time(22, 0))
    assert s2.tick(at(18, 22, 5)) == []                                # restart: no resend


def test_reminder_survives_restart(tmp_path):
    evs = [timed("Dis", 18, 15, 0)]
    s = Scheduler(time(8, 30), time(19, 0), lambda now: evs, StubNotifier(), State(tmp_path / "s.json"))
    assert len(s.tick(at(18, 14, 35))) == 1
    s2 = Scheduler(time(8, 30), time(19, 0), lambda now: evs, StubNotifier(), State(tmp_path / "s.json"))
    assert s2.tick(at(18, 14, 40)) == []


def test_i18n_dates():
    assert i18n.fmt_date(at(20)) == "Cuma 20 Kasım"
    assert i18n.fmt_date_full(at(20)) == "Cuma 20 Kasım 2026"
    assert i18n.fmt_left(90) == "1 saat 30 dk"
