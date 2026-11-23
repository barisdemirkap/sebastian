from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sebastian import planning

TZ = ZoneInfo("Europe/Istanbul")


def at(h, m=0, d=20):
    return datetime(2026, 11, d, h, m, tzinfo=TZ)


def test_asks_for_plan():
    assert planning.asks_for_plan("bu gorevleri de planla")
    assert not planning.asks_for_plan("cop aksam 18.30da cikacak planla")     # a clock time is a statement, not a request
    assert not planning.asks_for_plan("yarinin plani nasil")


def test_place_skips_busy_and_spills_to_next_day():
    busy = [(at(10), at(12))]
    drafts, left = planning.place(["Acme kontrol", "Araç randevu"], busy, at(9, 30), at(9, 30).date(), TZ)
    assert not left and all(not (d.start < at(12) and d.end > at(10)) for d in drafts)
    full = [(at(9), at(21))]
    drafts, left = planning.place(["Araç randevu"], full, at(8), at(8).date(), TZ)
    assert drafts[0].start.date() == at(8).date() + timedelta(days=1) and not left
