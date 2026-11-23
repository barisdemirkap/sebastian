"""ADR 0015: fuzzy times in the add flow, and the all-day default for to-do style items."""
import pytest
from test_assistant import make, say

CHORES = ["çorapları topla cuma ekle", "bulaşık yıkama cuma ekle", "süt al cuma ekle", "dolabı temizle cuma ekle", "çöpü çıkar cuma ekle"]
APPOINTMENTS = ["doktor cuma ekle", "toplantı cuma ekle", "randevu cuma ekle", "Ayşe ile buluşma cuma ekle", "müşteri görüşmesi cuma ekle"]


@pytest.mark.parametrize("text", CHORES)
def test_chore_with_a_day_is_all_day_and_says_so(text):
    a, *_ = make()
    r = say(a, text)
    assert "tüm gün" in r and "Saat belirtmediniz, tüm gün ekledim; saat söylerseniz düzeltirim." in r and "Saat kaçta" not in r


@pytest.mark.parametrize("text", APPOINTMENTS)
def test_appointment_still_asks_for_the_time(text):
    a, *_ = make()
    assert say(a, text).startswith("Saat kaçta")


@pytest.mark.parametrize("answer,span", [("4 ile 5 arası", "16:00-17:00"), ("4-5 civarı", "16:00-17:00"), ("akşama doğru", "18:00-19:00"),
                                         ("öğle yemeğinde", "12:30-13:30"), ("akşamüstü", "17:00-18:00"), ("sabah erken", "07:00-08:00")])
def test_fuzzy_answer_to_the_time_question(answer, span):
    a, *_ = make()
    say(a, "toplantı cuma ekle")
    r = say(a, answer)
    assert f"Cuma 20 Kasım 2026 {span}" in r and "Saati şöyle anladım" in r and "Saat kaçta" not in r


def test_midnight_is_the_start_of_the_next_day():
    a, *_ = make()
    say(a, "toplantı cuma ekle")
    assert "Cumartesi 21 Kasım 2026 00:00-01:00" in say(a, "gece yarısı")


def test_fuzzy_range_in_the_first_message_is_not_part_of_the_title():
    a, *_ = make()
    r = say(a, "cuma öğleden sonra 4-5 gibi toplantı ekle")
    assert "Toplantı\nCuma 20 Kasım 2026 16:00-17:00" in r


def test_stated_duration_after_all_day_default_edit():
    a, *_ = make()
    say(a, "çorapları topla cuma ekle")
    r = say(a, "akşama doğru bir saat sürsün")
    assert "Cuma 20 Kasım 2026 18:00-19:00" in r


def test_edit_time_of_all_day_draft_uses_chore_default_duration():
    a, *_ = make()
    say(a, "çöpü çıkar cuma ekle")
    assert "Cuma 20 Kasım 2026 10:00-10:15" in say(a, "saati 10:00 yap")
