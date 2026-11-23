"""ADR 0007: inline buttons under code-rendered previews; every tap verified, atomic, stale taps harmless."""
import re
from datetime import timedelta
from test_bot import make, upd, OWNER, NOW, IST
from test_assistant import ev
from sebastian.bot import group_updates
from sebastian.providers import FixtureProvider
from sebastian.telegram import TelegramApi

STALE = "Bu önizleme eskidi."


def cb(uid, data, chat=OWNER, user=OWNER, mid=101, ctype="private"):
    return {"update_id": uid, "callback_query": {"id": f"c{uid}", "from": {"id": user}, "data": data,
                                                  "message": {"message_id": mid, "chat": {"id": chat, "type": ctype}}}}


def data_of(api, i=-1):
    return [b["callback_data"] for b in api.markups[i][1]["inline_keyboard"][0]]


def preview(bot, uid=1, text="yarın saat 15:00 diş hekimi ekle"):
    bot.process_update(upd(uid, text))


def test_add_preview_has_save_edit_cancel_with_id_and_hash():
    bot, api, st, _ = make()
    preview(bot)
    kb = api.markups[-1][1]["inline_keyboard"][0]
    assert [b["text"] for b in kb] == ["Kaydet", "Düzenle", "İptal"]
    pid = st.open_pending(OWNER)["id"]
    h = st.open_pending(OWNER)["args_hash"][:8]
    assert [b["callback_data"] for b in kb] == [f"k:{pid}:{h}", f"e:{pid}:{h}", f"x:{pid}:{h}"]
    assert all(len(b["callback_data"]) <= 64 for b in kb)


def test_save_tap_writes_once_and_double_tap_is_safe():
    hub = FixtureProvider([])
    bot, api, st, _ = make(hub=hub)
    preview(bot)
    mid, k = api.markups[-1][0], data_of(api)[0]
    bot.process_update(cb(2, k, mid=mid))
    assert api.sent[-1][1].startswith("Emredersiniz, efendim. Ekledim: Diş hekimi")
    assert (OWNER, mid) in api.removed
    bot.process_update(cb(3, k, mid=mid))                      # second tap
    assert api.callback_answers[-1] == ("c3", STALE)
    assert len(hub.events(NOW, NOW + timedelta(days=5))) == 1


def test_tap_racing_typed_evet_executes_once():
    hub = FixtureProvider([])
    bot, api, st, _ = make(hub=hub)
    preview(bot)
    k = data_of(api)[0]
    bot.process_update(upd(2, "evet"))
    bot.process_update(cb(3, k))
    assert api.callback_answers[-1][1] == STALE and len(hub.events(NOW, NOW + timedelta(days=5))) == 1


def test_cancel_and_edit_buttons():
    bot, api, st, _ = make()
    preview(bot)
    d = data_of(api)
    bot.process_update(cb(2, d[1]))
    assert api.sent[-1][1] == 'Ne değişsin, efendim? Örnek: "saati 16:00 yap".' and st.open_pending(OWNER) is not None
    bot.process_update(upd(3, "saati 16:00 yap"))
    assert api.sent[-2][1] == "Önizlemeyi güncelledim." and len(api.markups) == 2
    bot.process_update(cb(4, d[0]))                            # the OLD save button is dead
    assert api.callback_answers[-1][1] == STALE and st.open_pending(OWNER) is not None
    bot.process_update(cb(5, data_of(api)[2]))
    assert api.sent[-1][1].startswith("Pekâlâ, efendim, vazgeçtim") and st.open_pending(OWNER) is None


def test_old_keyboard_is_removed_when_preview_replaced_or_typed_cancel():
    bot, api, st, _ = make()
    preview(bot)
    first = api.markups[-1][0]
    bot.process_update(upd(2, "saati 16:00 yap"))
    assert (OWNER, first) in api.removed
    second = api.markups[-1][0]
    bot.process_update(upd(3, "hayır"))
    assert (OWNER, second) in api.removed and st.get_kb(OWNER) is None


def test_reoffer_keeps_the_keyboard():
    bot, api, st, _ = make()
    preview(bot)
    bot.process_update(upd(2, "emin misin"))
    assert api.removed == [] and st.get_kb(OWNER) is not None


def test_delete_preview_has_delete_and_cancel_only():
    hub = FixtureProvider([ev("Diş hekimi", 19, 15, 0)])
    bot, api, st, _ = make(hub=hub)
    bot.process_update(upd(1, "diş hekimini sil"))
    kb = api.markups[-1][1]["inline_keyboard"][0]
    assert [b["text"] for b in kb] == ["Sil", "İptal"]
    pid, h = st.open_pending(OWNER)["id"], st.open_pending(OWNER)["args_hash"][:8]
    bot.process_update(cb(2, f"k:{pid}:{h}"))                   # a save tap never deletes
    assert api.callback_answers[-1][1] == STALE and len(hub.events(NOW, NOW + timedelta(days=5))) == 1
    bot.process_update(cb(3, kb[0]["callback_data"]))
    assert api.sent[-1][1].startswith("Emredersiniz, efendim. Sildim") and hub.events(NOW, NOW + timedelta(days=5)) == []


def test_edit_event_preview_has_save_cancel():
    hub = FixtureProvider([ev("Diş hekimi", 19, 15, 0)])
    bot, api, st, _ = make(hub=hub)
    bot.process_update(upd(1, "diş hekimini cumaya ertele"))
    assert [b["text"] for b in api.markups[-1][1]["inline_keyboard"][0]] == ["Kaydet", "İptal"]


def test_wrong_hash_expired_other_user_and_garbage_are_harmless():
    hub = FixtureProvider([])
    bot, api, st, clock = make(hub=hub)
    preview(bot)
    pid = st.open_pending(OWNER)["id"]
    bot.process_update(cb(2, f"k:{pid}:deadbeef"))
    assert api.callback_answers[-1][1] == STALE
    bot.process_update(cb(3, f"k:{pid}:{st.open_pending(OWNER)['args_hash'][:8]}", user=777))
    bot.process_update(cb(4, f"k:{pid}:{st.open_pending(OWNER)['args_hash'][:8]}", chat=-5, ctype="group"))
    n = len(api.callback_answers)
    bot.process_update(cb(5, "zzz"))
    assert api.callback_answers[n:] == [("c5", "")]
    assert len(api.callback_answers) == n + 1 and st.open_pending(OWNER) is not None
    k = data_of(api)[0]
    clock.now = NOW + timedelta(minutes=11)
    bot.process_update(cb(6, k))
    assert api.callback_answers[-1][1] == STALE and hub.events(NOW, NOW + timedelta(days=5)) == []


def test_callbacks_are_deduped_and_never_merged():
    bot, api, st, _ = make()
    preview(bot)
    k = data_of(api)[0]
    bot.process_update(cb(2, k))
    bot.process_update(cb(2, k))                                # redelivery
    assert len(api.callback_answers) == 1
    a = {"update_id": 1, "message": {"chat": {"id": OWNER}, "text": "x", "date": 100}}
    assert len(group_updates([a, cb(2, "k:1:aaaaaaaa"), a], OWNER)) == 3


def test_get_updates_allows_callback_query():
    sent = []
    api = TelegramApi("T", post=lambda url, payload, t: sent.append(payload) or {"ok": True, "result": []})
    api.get_updates(5)
    assert sent[0]["allowed_updates"] == ["message", "callback_query"]


def test_send_attaches_markup_to_the_last_chunk_and_returns_id():
    calls = []
    api = TelegramApi("T", post=lambda url, p, t: calls.append((url, p)) or {"ok": True, "result": {"message_id": 7}})
    assert api.send(1, "x" * 9000, {"inline_keyboard": []}) == 7
    assert "reply_markup" not in calls[0][1] and "reply_markup" in calls[-1][1]
    api.remove_keyboard(1, 7)
    api.answer_callback("c", "t", True)
    assert [u.rsplit("/", 1)[1] for u, _ in calls[-2:]] == ["editMessageReplyMarkup", "answerCallbackQuery"]


def test_button_taps_are_logged_as_inbound_messages():
    """Incident 2026-11-20 (msgs 149-150): a delete done by the Sil button had no inbound row, so the log read as if Sebastian deleted on its own."""
    hub = FixtureProvider([ev("Diş hekimi", 19, 15, 0)])
    bot, api, st, _ = make(hub=hub)
    bot.process_update(upd(1, "diş hekimini sil"))
    assert st.open_pending(OWNER) is not None
    k = data_of(api)[0]
    assert k.startswith("d:")
    bot.process_update(cb(2, k))
    rows = [(r["direction"], r["text"], r["answered"]) for r in st.recent_messages(OWNER, 20)]
    assert ("in", "[button:delete]", 1) in rows
    texts = [r["text"] for r in st.recent_messages(OWNER, 20)]
    assert texts.index("[button:delete]") < next(i for i, t in enumerate(texts) if t.startswith("Hemen, efendim") or "Sildim" in t)
    bot.process_update(cb(3, k))                                # a stale second tap is logged too and changes nothing
    assert [r["text"] for r in st.recent_messages(OWNER, 20)].count("[button:delete]") == 2
