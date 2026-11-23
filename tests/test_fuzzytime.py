"""ADR 0015: fuzzy Turkish times and ranges."""
import pytest
from sebastian import fuzzytime as fz

CASES = [
    ("öğleden sonra 4-5 gibi", "16:00", "17:00"), ("4 ile 5 arası", "16:00", "17:00"), ("dörtle beş arası", "16:00", "17:00"),
    ("4-5 civarı", "16:00", "17:00"), ("akşama doğru", "18:00", None), ("öğlene doğru", "12:00", None),
    ("sabah erken", "07:00", None), ("öğle", "12:00", None), ("akşamüstü", "17:00", None), ("akşam üstü", "17:00", None),
    ("gece yarısı", "00:00", None), ("kahvaltıda", "08:00", None), ("öğle yemeğinde", "12:30", None),
    ("sabah 9-10 arası", "09:00", "10:00"), ("akşam 7 gibi", "19:00", None), ("10-2 arası", "10:00", "14:00"),
    ("saat 4 civarı", "16:00", None),
]


@pytest.mark.parametrize("text,start,end", CASES)
def test_phrases(text, start, end):
    r = fz.find(text)
    assert r and f"{r.start:%H:%M}" == start and (f"{r.end:%H:%M}" if r.end else None) == end, text


def test_durations():
    assert fz.find("yarım saat").duration == 30 and fz.find("yarım saat").start is None
    assert fz.find("bir saat sürsün").duration == 60
    r = fz.find("akşama doğru iki saat sürsün")
    assert f"{r.start:%H:%M}" == "18:00" and r.duration == 120


def test_midnight_is_the_end_of_the_day_and_said_keeps_the_words():
    assert fz.find("cuma gece yarısı").next_day and fz.find("öğleden sonra 4-5 gibi").said == "öğleden sonra 4-5 gibi"
    assert fz.clock(fz.find("4-5 civarı")) == "16:00-17:00"


@pytest.mark.parametrize("text", ["4-5 Kasım", "20.11.2026", "1 saat önce hatırlat", "yarım saat önce", "çorap düzenle", "öğleden sonra", "4-5-2026"])
def test_not_a_time(text):
    assert fz.find(text) is None, text
