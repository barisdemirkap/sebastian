from datetime import date, time
import pytest
from sebastian.timeparse import find_date, find_time, find_reminder, find_duration, mentions_time, mentions_date, strip_spans

TODAY = date(2026, 11, 18)  # Wednesday


@pytest.mark.parametrize("text,expected", [
    ("bugün", date(2026, 11, 18)), ("yarın akşam", date(2026, 11, 19)), ("öbür gün", date(2026, 11, 20)),
    ("3 gün sonra", date(2026, 11, 21)), ("haftaya", date(2026, 11, 25)), ("cuma", date(2026, 11, 20)),
    ("Çarşamba", date(2026, 11, 25)), ("haftaya cuma", date(2026, 11, 27)), ("cumartesi günü", date(2026, 11, 21)),
    ("20.11.2026", date(2026, 11, 20)), ("20.11", date(2026, 11, 20)), ("3 Mart", date(2027, 3, 3)),
    ("3 mart 2028", date(2028, 3, 3)), ("25 Kasım'da", date(2026, 11, 25)), ("5/12", date(2026, 12, 5)),
    ("1 Ocak", date(2027, 1, 1)),
])
def test_dates(text, expected):
    assert find_date(text, TODAY)[0] == expected


def test_no_date():
    assert find_date("diş hekimi randevusu", TODAY) is None
    assert find_date("30.02", TODAY) is None


@pytest.mark.parametrize("text,expected", [
    ("saat 15:30", time(15, 30)), ("15.30'da", time(15, 30)), ("saat 3'te", time(3, 0)),
    ("akşam saat 7", time(19, 0)), ("akşam 7'de", time(19, 0)), ("öğleden sonra 3te", time(15, 0)),
    ("saat üçte", time(3, 0)), ("ikide", time(2, 0)), ("saat 9", time(9, 0)), ("saat 3 buçuk", time(3, 30)),
    ("öğlen", time(12, 0)), ("sabah 10:15", time(10, 15)), ("gece 11'de", time(23, 0)), ("21:00", time(21, 0)),
])
def test_times(text, expected):
    assert find_time(text)[0] == expected


def test_time_never_invented():
    assert find_time("yarın doktor") is None
    assert find_time("akşam") is None
    assert find_time("sabah") is None
    assert find_time("20.11") is None          # a date, not a time
    assert find_time("25 kasım") is None


def test_date_and_time_together():
    t = "20.11 saat 15.30"
    assert find_date(t, TODAY)[0] == date(2026, 11, 20)
    assert find_time(t)[0] == time(15, 30)


def test_reminder_and_duration():
    assert find_reminder("bir saat önce hatırlat")[0] == 60
    assert find_reminder("45 dk önce")[0] == 45
    assert find_reminder("1 gün önce hatırlat")[0] == 1440
    assert find_reminder("yarın 3te") is None
    r = find_reminder("toplantı 1 saat önce hatırlat")
    assert find_duration("toplantı 1 saat önce hatırlat", r[1]) is None
    assert find_duration("2 saat sürecek")[0] == 120
    assert find_duration("yarım saat")[0] == 30
    assert find_duration("45 dakika")[0] == 45


def test_guards():
    assert mentions_time("yarın saat 15:00") and mentions_time("ucte") and not mentions_time("yarın doktor")
    assert mentions_date("yarın") and mentions_date("3 mart") and not mentions_date("doktor randevusu ekle")


def test_strip_spans_keeps_original_text():
    text = "Yarın saat 15:00 Diş Hekimi ekle"
    d, ds = find_date(text, TODAY)
    t, ts = find_time(text)
    assert strip_spans(text, ds + ts).split() == ["Diş", "Hekimi", "ekle"]
