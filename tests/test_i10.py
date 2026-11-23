"""ADR 0011: invariant I10 (a yes/no question always has an action or an open question behind it) and removal wordings."""
import re
from sebastian import i18n, nl as nlmod, rules
from sebastian.textutil import fold
from test_assistant import make, say, ev, FakeProv, NOW, OWNER
from datetime import date

Q = 'Perşembe günündeki "Çamaşır" etkinliği hâlâ duruyor. Silmemi ister misin?'


def model(q):
    return nlmod.NLService("claude", {"claude": FakeProv({"intent": "unclear", "question": q}), "qwen": FakeProv(exc=RuntimeError("x"))},
                           consent=True)


def test_yes_no_question_pattern_covers_the_incident_wordings():
    for q in ("Silmemi ister misin?", "Ekleyeyim mi?", "Bir hatırlatıcı ekleyeyim mi?", "Silsem mi?", "Kaydedeyim mi?"):
        assert re.search(i18n.YES_NO_QUESTION, fold(q)), q
    for q in ("Hangi gün?", "Ne yapmamı istersin?", "Adını yazar mısın?"):
        assert not re.search(i18n.YES_NO_QUESTION, fold(q)), q


def test_model_delete_offer_becomes_a_pending_delete_preview():
    a, hub, st, _ = make([ev("Çamaşır", 19, 10)], nl=model(Q))
    out = say(a, "camasiri persembe gununden kaldirdin mi?")
    assert "SİLMEMİ" in out and "Silmemi ister misin" not in out
    assert st.open_pending(OWNER)["intent"] == "delete_event"
    assert "evet sil" in say(a, "evet")                       # never 'nothing pending'
    assert say(a, "evet sil").startswith("Emredersiniz, efendim. Sildim")
    assert hub.events(NOW, NOW.replace(day=25)) == []


def test_model_offer_without_an_action_is_rephrased_and_evet_gets_the_question_back():
    a, hub, st, _ = make([], nl=model("Bir hatırlatıcı ekleyeyim mi?"))
    out = say(a, "şu iş ne olacak acaba")
    assert out == i18n.t("open_question") and st.open_pending(OWNER) is None
    assert st.get_conversation(OWNER)["awaiting"] == "clarify"
    back = say(a, "evet")
    assert back.startswith("Bu bir evet/hayır sorusu değildi.") and "Onay bekleyen" not in back


def test_an_open_model_question_is_passed_on_unchanged():
    a, _, st, _ = make([], nl=model("Hangi etkinliği kastediyorsun?"))
    assert say(a, "şunu halledelim") == "Hangi etkinliği kastediyorsun?"
    assert st.get_conversation(OWNER)["question"] == "Hangi etkinliği kastediyorsun?"


def test_evet_with_nothing_open_still_says_nothing_pending():
    a, _, _, _ = make()
    assert say(a, "evet") == i18n.t("nothing_pending")


def test_removal_wordings_parse_to_a_delete_with_the_title_and_date():
    today = date(2026, 11, 18)
    for text in ("camasiri kaldir", "camasiri persembe gununden kaldirdin mi?", "camasir kalmasin", "camasiri cikar",
                 "camasira gerek yok", "camasiri iptal et", "camasiri sil"):
        p = rules.parse(text, today)
        assert p["intent"] == "delete_event" and fold(p["match"]).startswith("camasir"), (text, p)
    assert rules.parse("camasiri persembe gununden kaldirdin mi?", today)["date"] == "2026-11-19"


def test_gerek_yok_in_an_add_request_is_not_a_delete():
    assert rules.parse("camasira gerek yok sadece alexa kurulumu ekle persembe 16:00", date(2026, 11, 18))["intent"] == "add_event"
