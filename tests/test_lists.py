"""ADR 0016: named lists (Alınacaklar): wordings, preview-gated removal, buttons, digest, operator seeding."""
import json
from datetime import datetime, timedelta
import pytest
from test_assistant import IST, NOW, OWNER, ev, make, say
from test_buttons import cb, data_of
from test_bot import make as make_bot, upd
from sebastian import lists
from sebastian.digest import morning_digest


def texts(st, status="open"):
    return [r["text"] for r in st.list_items(OWNER, "alinacaklar", status)]


def test_parse_wordings():
    p = lists.parse
    assert p("alınacaklara üçlü priz ve kahve ekle") == {"kind": "add", "list": "alinacaklar", "items": ["Üçlü priz", "Kahve"]}
    assert p("alışveriş listesine süt, yumurta ekle")["items"] == ["Süt", "Yumurta"]
    assert p("market listesine 2 litre süt ekle")["items"] == ["2 litre süt"]
    assert p("süt ve ekmek listeme ekle")["items"] == ["Süt", "Ekmek"]
    for t in ("listemde ne var", "alışveriş listem", "alınacaklar", "alınacaklar listesi", "listeyi göster", "market listem"):
        assert p(t) == {"kind": "show", "list": "alinacaklar"}, t
    assert p("yumurtayı aldım") == {"kind": "check", "match": "Yumurtayı"} and p("hepsini aldım") == {"kind": "check_all"}
    assert p("kahveyi listeden çıkar")["kind"] == "remove" and p("listeyi temizle") == {"kind": "clear", "list": "alinacaklar"}


@pytest.mark.parametrize("text", ["market alışverişi yarın 17:00 ekle", "markete gidiyorum", "marketi sil", "toplantıyı sil", "çöpü çıkar",
                                  "yarın 15:00 diş hekimi ekle", "market", "alışveriş listesi ekle"])
def test_other_messages_are_not_list_commands(text):
    assert lists.parse(text) is None


def test_add_splits_keeps_quantity_merges_duplicates_and_numbers_the_view():
    a, _, st, _ = make()
    r = say(a, "alınacaklara üçlü priz ve kahve ekle")
    assert "Alınacaklar listesine ekledim: Üçlü priz, Kahve." in r and "1. ☐ Üçlü priz\n2. ☐ Kahve" in r
    r = say(a, "alışveriş listesine 2 litre süt, kahve, süt ekle")
    assert "ekledim: 2 litre süt." in r and "Zaten listede, tekrar eklemedim: Kahve." in r and texts(st) == ["Üçlü priz", "Kahve", "2 litre süt"]


def test_show_empty_and_commands():
    a, *_ = make()
    assert say(a, "listemde ne var") == "Alınacaklar listesi boş." and say(a, "/liste") == say(a, "/alinacaklar")
    say(a, "alınacaklara süt ekle")
    assert say(a, "/liste") == "Alınacaklar:\n1. ☐ Süt"


def test_check_off_moves_to_done_section_and_leaves_the_view_next_day():
    a, _, st, clock = make()
    say(a, "alınacaklara yumurta, kahve ekle")
    r = say(a, "yumurtayı aldım")
    assert "Aldım olarak işaretledim: Yumurta." in r and "1. ☐ Kahve\nAlındı:\n2. ✔ Yumurta" in r
    assert texts(st) == ["Kahve"] and texts(st, "done") == ["Yumurta"]
    clock.now += timedelta(days=1)
    assert say(a, "listem") == "Alınacaklar:\n1. ☐ Kahve"


def test_check_by_number_and_unknown_item_falls_through():
    a, _, st, _ = make()
    say(a, "alınacaklara süt, kahve, çay ekle")
    assert "Aldım olarak işaretledim: Süt, Çay." in say(a, "1 ve 3 aldım")
    assert say(a, "hepsini aldım").startswith("Hepsini aldım olarak işaretledim.") and texts(st) == []
    assert "Aldım olarak" not in say(a, "ekmeği aldım")


def test_remove_needs_preview_and_confirm():
    a, _, st, _ = make()
    say(a, "alınacaklara süt, kahve ekle")
    r = say(a, "kahveyi listeden çıkar")
    assert r == "Alınacaklar listesinden şunu çıkarayım mı?\n- Kahve\n(evet / hayır)" and texts(st) == ["Süt", "Kahve"]
    assert "Listeden çıkardım: Kahve." in say(a, "evet") and texts(st) == ["Süt"]
    assert "Listede bulamadım: Peynir" in say(a, "peyniri listeden çıkar")


def test_remove_can_be_cancelled():
    a, _, st, _ = make()
    say(a, "alınacaklara süt ekle")
    say(a, "sütü listeden çıkar")
    assert "vazgeçtim" in say(a, "hayır") and texts(st) == ["Süt"]


def test_clear_confirms_and_removes_open_and_done():
    a, _, st, _ = make()
    say(a, "alınacaklara süt, kahve ekle")
    say(a, "kahveyi aldım")
    assert say(a, "listeyi temizle").startswith("Alınacaklar listesini temizleyeyim mi? (2 madde)")
    assert say(a, "evet") == "Alınacaklar listesini temizledim." and texts(st) == [] and texts(st, "done") == []
    assert say(a, "listeyi temizle") == "Alınacaklar listesi zaten boş."


def test_lists_are_per_owner():
    a, _, st, _ = make()
    st.add_list_item(999, "alinacaklar", "Başkasının", NOW)
    say(a, "alınacaklara süt ekle")
    assert say(a, "listem") == "Alınacaklar:\n1. ☐ Süt"


def test_a_calendar_preview_survives_a_list_add():
    a, _, st, _ = make()
    say(a, "yarın saat 15:00 diş hekimi ekle")
    pid = st.open_pending(OWNER)["id"]
    assert "ekledim: Süt" in say(a, "alınacaklara süt ekle") and st.open_pending(OWNER)["id"] == pid
    assert a.keyboard() is None


# ---- buttons, bot level
def list_btns(api, i=-1):
    """The list view's all-bought/clear row: the LAST row, under the per-item tap rows (ADR 0025)."""
    return api.markups[i][1]["inline_keyboard"][-1]


def test_list_message_carries_buttons_that_are_verified():
    bot, api, st, _ = make_bot()
    bot.process_update(upd(1, "alınacaklara süt, kahve ekle"))
    assert [b["text"] for b in list_btns(api)] == ["Tümü alındı", "Temizle"]
    assert [r[0]["text"] for r in api.markups[-1][1]["inline_keyboard"][:-1]] == ["☐ Süt", "☐ Kahve"]
    mid, (all_, clr) = api.markups[-1][0], [b["callback_data"] for b in list_btns(api)]
    bot.process_update(upd(2, "alınacaklara çay ekle"))               # the list changed: the old buttons are stale
    bot.process_update(cb(3, all_, mid=mid))
    assert api.callback_answers[-1] == ("c3", "Bu önizleme eskidi.") and texts(st) == ["Süt", "Kahve", "Çay"]
    all2 = list_btns(api)[0]["callback_data"]
    bot.process_update(cb(4, all2, mid=api.markups[-1][0]))
    assert texts(st) == [] and texts(st, "done") == ["Süt", "Kahve", "Çay"] and "Hepsini aldım" in api.sent[-1][1]


def test_clear_button_opens_the_confirm_preview_and_never_clears_alone():
    bot, api, st, _ = make_bot()
    bot.process_update(upd(1, "alınacaklara süt ekle"))
    clr = list_btns(api)[1]["callback_data"]
    bot.process_update(cb(2, clr, mid=api.markups[-1][0]))
    assert texts(st) == ["Süt"] and st.open_pending(OWNER)["intent"] == "list_clear" and "temizleyeyim mi" in api.sent[-1][1]
    assert [b["text"] for b in api.markups[-1][1]["inline_keyboard"][0]] == ["Kaydet", "İptal"]
    bot.process_update(cb(3, data_of(api)[0], mid=api.markups[-1][0]))
    assert texts(st) == [] and "listesini temizledim" in api.sent[-1][1]


def test_forged_list_callback_is_stale():
    bot, api, st, _ = make_bot()
    bot.process_update(upd(1, "alınacaklara süt ekle"))
    for data in ("a:1:00000000", "c:1:deadbeef", "a:9:00000000"):
        bot.process_update(cb(10 + len(data), data))
        assert api.callback_answers[-1][1] == "Bu önizleme eskidi."
    assert texts(st) == ["Süt"]


# ---- digest
def test_morning_digest_lists_items_only_on_a_shopping_day():
    now = datetime(2026, 11, 19, 8, 30, tzinfo=IST)
    lst = [("Alınacaklar", ["Süt", "Kahve"])]
    plain = [ev("Toplantı", 19, 10)]
    shop = [ev("Market alışverişi", 19, 18), ev("Toplantı", 19, 10)]
    assert "Alınacaklar" not in morning_digest(plain, now, lists=lst)
    assert "Alınacaklar (alışveriş günü):\n- Süt\n- Kahve" in morning_digest(shop, now, lists=lst)
    assert "Alınacaklar" not in morning_digest(shop, now, lists=[("Alınacaklar", [])])
    assert "Alınacaklar" in morning_digest([ev("alışveriş", 19, 18)], now, lists=lst)


def test_scheduler_passes_lists_to_the_morning_digest():
    from sebastian.notify import StubNotifier
    from sebastian.scheduler import Scheduler, State
    from datetime import time
    now = datetime(2026, 11, 19, 8, 30, tzinfo=IST)
    n = StubNotifier()
    Scheduler(time(8, 30), time(20, 0), lambda t: [ev("Market", 19, 18)], n, State(None), lists=lambda: [("Alınacaklar", ["Süt"])]).tick(now)
    assert "Alınacaklar (alışveriş günü):\n- Süt" in n.sent[0]


def test_help_texts_mention_lists_and_fuzzy_times():
    a, *_ = make()
    assert "alınacaklara süt ekle" in say(a, "/yardim") and "/liste" in say(a, "/komutlar")
    g = say(a, "/rehber")
    assert "listeyi temizle" in g and "öğleden sonra 4-5 gibi" in g and "Zaten takvimde olan" in g


def test_list_is_its_own_topic_msg_928():
    """Incident 2026-11-20 12:25: a typo'd 'update the list' opened no list mode, 'X ekle' became a calendar event asking for
    a date, and 'kahve ekmek ve domates alindi' was swallowed by that draft."""
    a, _, st, _ = make()
    assert lists.parse("alincaklar listesini guncelleyelim") == {"kind": "open", "list": "alinacaklar"}
    r = say(a, "alınacaklar listesini güncelleyelim")
    assert "listesi açık" in r
    r = say(a, "soğuk silikon ekle")
    assert "Hangi gün" not in r and "Soğuk silikon" in texts(st)
    say(a, "kahve ve ekmek ekle")
    r = say(a, "kahve ekmek ve soğuk silikon alındı")
    assert texts(st) == [] and "Hangi tarih" not in r
    assert a.store.get_conversation(OWNER)["awaiting"] == "list"
    say(a, "yarın 15:00 berber ekle")          # a calendar wording leaves list mode
    assert (a.store.get_conversation(OWNER) or {}).get("awaiting") != "list"


# ---- per-item tap buttons (ADR 0025)
def test_tapping_a_list_item_toggles_it_and_a_forged_tap_is_stale():
    bot, api, st, _ = make_bot()
    bot.process_update(upd(1, "alınacaklara süt, kahve ekle"))
    tap = api.markups[-1][1]["inline_keyboard"][0][0]["callback_data"]
    bot.process_update(cb(2, tap, mid=api.markups[-1][0]))
    assert texts(st) == ["Kahve"] and texts(st, "done") == ["Süt"]
    bot.process_update(cb(3, "i:1:deadbeef"))
    assert api.callback_answers[-1] == ("c3", "Bu önizleme eskidi.") and texts(st) == ["Kahve"]


def test_tapping_a_task_closes_it_and_a_forged_tap_is_stale():
    a, _, st, _ = make()
    t1, t2 = st.add_task(OWNER, "Çöpü çıkar", NOW), st.add_task(OWNER, "Odayı topla", NOW)
    h = a._item_hash(t1, "Çöpü çıkar")
    a.callback(OWNER, "t", t1, h)
    assert st.get_task(t1)["status"] == "done" and st.get_task(t2)["status"] == "open"
    assert a.callback(OWNER, "t", t2, "deadbeef")[0] == "Bu önizleme eskidi." and st.get_task(t2)["status"] == "open"
