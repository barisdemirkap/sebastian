import pytest

from sebastian.readytime import parse, subject_hit


def test_appointment_and_ready_time():
    r = parse("doktor randevusu tam 8.50de evden çıkmak için 8.10da hazır olalım")
    assert r == {"ready": (8, 10), "appt": (8, 50), "leave": True, "named": True, "retime": False}


def test_leave_phrase_without_the_word_randevu_is_not_named():
    assert parse("doktor 8.50de evden çıkmak için 8.10da hazır olalım")["named"] is False


def test_ready_only_and_retime_only():
    assert parse("toplantı için 14.40ta hazır olalım") == {"ready": (14, 40), "appt": None, "leave": False, "named": False, "retime": False}
    assert parse("toplantı 15.30a alındı")["appt"] == (15, 30)


@pytest.mark.parametrize("text", ["yarın 15:00 diş hekimi", "toplantı 15.30", "toplantıyı 15.30a ekle", "ne zaman 8.10da hazır olalım?"])
def test_ordinary_texts_are_not_readings(text):
    assert parse(text) is None


@pytest.mark.parametrize("text,title", [
    ("doktor randevusu", "Aile hekimi: sağlık raporu al"), ("diş hekimi randevusu 16.30", "Diş hekimi"),
    ("toplantı 11.15e alındı", "Haftalık toplantı"), ("araç muayenesi 13.20de", "Araç muayenesi"), ("çamaşır 18.30a kaldı", "Çamaşır yıka"),
])
def test_subject_hit(text, title):
    assert subject_hit(text, title)


def test_generic_words_are_not_a_subject():
    assert not subject_hit("hazır olalım 8.10da randevu için", "Aile hekimi")
    assert not subject_hit("toplantı 11.15e alındı", "Çamaşır yıka")
