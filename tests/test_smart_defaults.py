"""Smart defaults (ADR 0014): a time without a date takes the next upcoming occurrence; short tasks last 15 minutes."""
from datetime import datetime
from test_assistant import IST, OWNER, make, say


def at(clock, h, m=0):
    clock.now = datetime(2026, 11, 19, h, m, tzinfo=IST)     # Thursday


def test_morning_time_with_no_date_is_today_when_still_ahead():
    a, _, _, clock = make()
    at(clock, 5, 24)
    r = say(a, "çöpü cikarmayi sabah 7.30'a ekle")
    assert "Çöpü cikarmayi\nBugün Perşembe 19 Kasım 2026 07:30-07:45" in r
    assert "Hangi gün" not in r and "Gün belirtmediğiniz için en yakın 07:30 saatini aldım" in r


def test_time_that_has_passed_is_tomorrow():
    a, _, _, clock = make()
    at(clock, 8)
    assert "Yarın Cuma 20 Kasım 2026 07:30-07:45" in say(a, "sabah 7.30 çöpü çıkar ekle")


def test_time_forms_dotted_colon_dative_words():
    for text in ("çöp ekle 7:30", "çöp ekle sabah 7.30'a", "çöp ekle yedi buçuk", "çöp ekle sabah yedide", "çöp ekle saat 07:30"):
        a, _, _, clock = make()
        at(clock, 5, 24)
        want = "07:00-07:15" if text.endswith("yedide") else "07:30-07:45"
        assert f"Bugün Perşembe 19 Kasım 2026 {want}" in say(a, text), text


def test_evening_part_of_day_is_pm():
    a, _, _, clock = make()
    at(clock, 5, 24)
    assert "Bugün Perşembe 19 Kasım 2026 19:30-19:45" in say(a, "akşam 7.30 çöp ekle")
    say(a, "hayır")
    assert "16:00-16:15" in say(a, "öğleden sonra 4'e kargo ekle")


def test_appointment_keeps_sixty_minutes():
    a, _, _, clock = make()
    at(clock, 5, 24)
    assert "Bugün Perşembe 19 Kasım 2026 09:00-10:00" in say(a, "saat 09:00 diş hekimi ekle")


def test_explicit_duration_wins_over_the_chore_default():
    a, _, _, clock = make()
    at(clock, 5, 24)
    assert "07:30-08:00" in say(a, "çöp ekle sabah 7.30 30 dakika")


def test_hour_one_to_six_is_asked_only_when_both_readings_are_ahead():
    a, hub, _, clock = make()
    at(clock, 2)
    q = say(a, "saat 4 çöp ekle")
    assert "1) Bugün 04:00" in q and "2) Bugün 16:00" in q
    assert "16:00-16:15" in say(a, "16:00")
    a, _, _, clock = make()
    at(clock, 17)
    assert "1) Yarın 04:00" in say(a, "saat 4 çöp ekle")
    assert "Yarın Cuma 20 Kasım 2026 04:00-04:15" in say(a, "sabah")


def test_day_question_accepts_number_word_weekday_and_date():
    for answer, want in (("1", "Bugün Perşembe 19 Kasım"), ("2", "Yarın Cuma 20 Kasım"), ("bugün", "Bugün Perşembe"),
                         ("yarın", "Yarın Cuma"), ("cuma", "Cuma 20 Kasım"), ("25 Kasım", "Çarşamba 25 Kasım")):
        a, _, _, clock = make()
        at(clock, 5, 24)
        say(a, "toplantı ekle")
        assert say(a, answer).startswith("Saat kaçta"), answer
        assert want in say(a, "15:00"), answer


def test_restating_never_repeats_the_question_and_skips_the_menu():
    a, _, _, clock = make()
    at(clock, 5, 24)
    first = say(a, "çöp ekle")
    r = say(a, "tekrar tekrar söylüyorum")
    assert r.startswith("Şöyle anladım: Çöp.\nHangi") and r != first
    assert "Takıldık" not in r


def test_date_asked_with_a_time_in_the_answer_is_taken():
    a, _, _, clock = make()
    at(clock, 5, 24)
    say(a, "çöp ekle")
    r = say(a, "sabah 7.30 dedim ya")
    assert r.startswith("Haklısınız, saati aldım.\nŞunu takvime işliyorum, onaylar mısınız?") and "Bugün Perşembe 19 Kasım 2026 07:30-07:45" in r


def test_presence_check_during_a_question_is_answered_once_in_short_form():
    a, _, st, clock = make()
    at(clock, 5, 24)
    say(a, "toplantı ekle")          # an appointment-like title keeps the time question (a chore would go all-day, ADR 0015)
    r = say(a, "aloo orda misin")
    assert r == "Buradayım, efendim. Hangi gün?\n1) Bugün\n2) Yarın\n3) Başka gün"
    assert st.get_conversation(OWNER)["awaiting"] == "date"          # the question stays open, untouched
    assert say(a, "cevap versene").startswith("Buradayım, efendim.")
    assert say(a, "aloo") != ""
    assert say(a, "yarın").startswith("Saat kaçta")


def test_presence_check_without_a_question_is_just_presence():
    a, *_ = make()
    assert say(a, "orda misin") == "Buradayım, efendim."


def test_expired_preview_is_reshown_only_on_a_confirmation_and_needs_a_fresh_yes():
    from datetime import timedelta
    a, hub, _, clock = make()
    at(clock, 5, 24)
    say(a, "yarın saat 15:00 berber ekle")
    clock.now += timedelta(minutes=11)
    assert "Yeniden gösteriyorum" in say(a, "evet")
    assert [e for e in hub._events] == []
    assert say(a, "evet").startswith("Emredersiniz, efendim. Ekledim: Berber")


def test_expired_preview_and_a_new_request_start_silently():
    from datetime import timedelta
    a, _, _, clock = make()
    at(clock, 5, 24)
    say(a, "yarın saat 15:00 berber ekle")
    clock.now += timedelta(minutes=11)
    r = say(a, "bugün ne var")
    assert "süresi dolmuştu" not in r and "zaman aşımı" not in r


def test_duration_estimates_default_half_hour_owner_msg_924():
    from sebastian.textutil import default_duration
    assert default_duration("Bilinmeyen bir iş") == 30
    assert default_duration("Çöpü çıkar") == 15
    assert default_duration("Doktor randevusu") == 60
    assert default_duration("Sınav") == 90
