"""ADR 0012: weekday names, today's weekday asks, previews show weekday and full date."""
from datetime import date, datetime
from sebastian import i18n
from sebastian.timeparse import find_date, names_today_weekday
from test_assistant import make, say, ev, IST, OWNER, Clock

THU = date(2026, 11, 19)


def thursday(hour=5, minute=1):
    clock = Clock()
    clock.now = datetime(2026, 11, 19, hour, minute, tzinfo=IST)
    return clock


def test_weekday_resolution_table():
    wed = date(2026, 11, 18)
    assert find_date("persembe", wed)[0] == THU                       # next occurrence
    assert find_date("persembe", THU)[0] == date(2026, 11, 26)        # today's name: next week, the assistant asks first
    assert find_date("bu persembe", THU)[0] == THU                    # 'this Thursday' includes today
    assert find_date("bu cuma", THU)[0] == date(2026, 11, 20)
    assert find_date("haftaya persembe", THU)[0] == date(2026, 11, 26)
    assert find_date("haftaya persembe", wed)[0] == date(2026, 11, 26)
    assert find_date("yarin", THU)[0] == date(2026, 11, 20)
    assert find_date("obur gun", THU)[0] == date(2026, 11, 21)
    assert find_date("pazartesi", THU)[0] == date(2026, 11, 23)


def test_names_today_weekday_only_for_the_bare_name():
    assert names_today_weekday("persembe ogleden sonra 4e", THU)
    assert names_today_weekday("persembeye", THU)
    for t in ("bu persembe", "haftaya persembe", "bugun persembe", "gelecek hafta persembe", "persembe 26.11", "cuma", "yarin"):
        assert not names_today_weekday(t, THU), t
    assert not names_today_weekday("persembe", date(2026, 11, 18))


def test_dative_hour_and_day_part_belong_to_the_time():
    from sebastian import rules
    p = rules.parse("persembe ogleden sonra 4e alexa kurulumu ekle", date(2026, 11, 18))
    assert p["time"] == "16:00" and p["title"] == "Alexa kurulumu"


def test_ask_then_today_or_next_week_by_word_or_number():
    for answer, shown in (("bugün", "Perşembe 19 Kasım 2026 16:00-16:30"), ("1", "Perşembe 19 Kasım 2026 16:00-16:30"),
                          ("haftaya", "Perşembe 26 Kasım 2026 16:00-16:30"), ("2", "Perşembe 26 Kasım 2026 16:00-16:30")):
        a, hub, st, _ = make(clock=thursday())
        q = say(a, "persembe ogleden sonra 4e alexa kurulumu ekle")
        assert q.startswith("Bugün Perşembe; bugün (19 Kasım) mi, haftaya (26 Kasım) mi?") and st.open_pending(OWNER) is None
        out = say(a, answer)
        assert shown in out and st.open_pending(OWNER) is not None, (answer, out)


def test_unrelated_answer_asks_again_and_evet_returns_the_question():
    a, _, st, _ = make(clock=thursday())
    q = say(a, "persembe ogleden sonra 4e alexa kurulumu ekle")
    assert say(a, "hmm") == i18n.t("pick_again_day")
    assert q in say(a, "evet")


def test_a_time_that_has_passed_today_means_next_week_without_asking():
    a, _, st, _ = make(clock=thursday(17, 0))
    out = say(a, "persembe 16:00 alexa kurulumu ekle")
    assert "Perşembe 26 Kasım 2026 16:00-16:30" in out and st.open_pending(OWNER) is not None


def test_bu_persembe_on_thursday_is_today_without_a_question():
    a, _, st, _ = make(clock=thursday())
    assert "Perşembe 19 Kasım 2026 10:00" in say(a, "bu perşembe 10'da berber ekle")


def test_a_named_weekday_that_is_not_today_never_asks():
    a, _, st, _ = make(clock=thursday())
    assert "Cuma 20 Kasım 2026 10:00" in say(a, "cuma 10'da berber ekle")


def test_delete_on_the_thursday_itself_finds_todays_event_and_next_weeks_too():
    a, hub, st, _ = make([ev("Çamaşır", 19, 10)], clock=thursday())
    assert "19 Kasım 2026" in say(a, "camasiri persembe gununden kaldir")
    a2, _, _, _ = make([ev("Çamaşır", 19, 10), ev("Çamaşır", 26, 10)], clock=thursday())
    assert say(a2, "camasiri persembe kaldir").startswith(i18n.t("ask_pick"))
