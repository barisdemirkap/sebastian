"""ADR 0002: every channel implements the Channel contract and drives the real bot with the preview + confirm gate intact."""
import pytest
from sebastian.channels import Channel, ConsoleChannel
from sebastian.telegram import FakeTelegram, TelegramApi
from test_bot import make as make_bot


def test_all_channels_satisfy_the_contract():
    for ch in (ConsoleChannel(), FakeTelegram(), TelegramApi("123:abc")):
        assert isinstance(ch, Channel), type(ch).__name__


def test_console_renders_buttons_as_numbers_and_maps_a_number_to_the_tap():
    out = []
    ch = ConsoleChannel(owner=7, write=out.append)
    ch.send(7, "Kaydedeyim mi?", {"inline_keyboard": [[{"text": "Kaydet", "callback_data": "k:1:abcdef12"}, {"text": "İptal", "callback_data": "x:1:abcdef12"}]]})
    assert "1) Kaydet" in out[-1] and "2) İptal" in out[-1]
    ch.feed("2")
    (u,) = ch.get_updates(None)
    assert u["callback_query"]["data"] == "x:1:abcdef12" and u["callback_query"]["from"]["id"] == 7
    ch.feed("yarın 15:00 diş hekimi ekle")
    (m,) = ch.get_updates(None)
    assert m["message"]["text"].startswith("yarın") and m["message"]["chat"]["type"] == "private"


def test_console_channel_drives_the_real_bot_through_a_preview():
    bot, _, st, _ = make_bot()
    out = []
    ch = ConsoleChannel(owner=bot.owner, write=out.append)
    bot.api = ch
    ch.feed("yarın saat 15:00 diş hekimi ekle")
    bot.step(poll_timeout=0)
    assert "diş hekimi" in out[-1].lower() and "1) Kaydet" in out[-1]
    assert st.open_pending(bot.owner) is not None          # nothing is written before the confirmation


def test_slack_channel_drives_the_real_bot_through_a_preview():
    from sebastian.channels import SlackChannel
    bot, _, st, _ = make_bot()
    sent, queue_ = [], []
    n = [0]

    def call(method, payload):
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D1"}}
        n[0] += 1
        sent.append(payload)
        return {"ok": True, "ts": f"1700000000.{n[0]:06d}"}
    bot.api = SlackChannel(bot.owner, "U1", call, lambda: [queue_.pop(0)] if queue_ else [])
    queue_.append({"type": "events_api", "payload": {"event": {"type": "message", "channel_type": "im", "channel": "D1", "user": "U1",
                                                                "text": "yarın saat 15:00 diş hekimi ekle", "ts": "1700000010.000100"}}})
    bot.step(poll_timeout=0)
    assert sent and "diş hekimi" in sent[-1]["text"].lower()
    assert [e["action_id"][0] for b in sent[-1]["blocks"] if b["type"] == "actions" for e in b["elements"]][0] == "k"
    assert st.open_pending(bot.owner) is not None              # a preview, nothing written yet


def _telegram_with_fake_transport(inbox):
    calls = []

    def post(url, payload, timeout):
        method = url.rsplit("/", 1)[-1]
        calls.append((method, payload))
        if method == "getUpdates":
            out = list(inbox)
            inbox.clear()
            return {"ok": True, "result": out}
        return {"ok": True, "result": {"message_id": 500 + len(calls)}}
    return TelegramApi("123:abc", post=post), calls


def test_telegram_adapter_drives_the_real_bot_through_a_preview():
    bot, _, st, _ = make_bot()
    inbox = [{"update_id": 1, "message": {"message_id": 9, "chat": {"id": bot.owner, "type": "private"}, "from": {"id": bot.owner},
                                          "date": 1700000000, "text": "yarın saat 15:00 diş hekimi ekle"}}]
    bot.api, calls = _telegram_with_fake_transport(inbox)
    bot.step(poll_timeout=0)
    sends = [p for m, p in calls if m == "sendMessage"]
    assert sends and "diş hekimi" in sends[-1]["text"].lower()
    assert sends[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].startswith("k")
    assert st.open_pending(bot.owner) is not None              # a preview, nothing written yet


def test_telegram_adapter_ignores_unknown_senders():
    bot, _, st, _ = make_bot()
    inbox = [{"update_id": 1, "message": {"message_id": 9, "chat": {"id": 999, "type": "private"}, "from": {"id": 999},
                                          "date": 1700000000, "text": "yarın saat 15:00 diş hekimi ekle"}}]
    bot.api, calls = _telegram_with_fake_transport(inbox)
    bot.step(poll_timeout=0)
    assert not [1 for m, _ in calls if m == "sendMessage"] and st.open_pending(bot.owner) is None


def test_telegram_adapter_splits_long_text_and_keeps_the_keyboard_on_the_last_chunk():
    api, calls = _telegram_with_fake_transport([])
    kb = {"inline_keyboard": [[{"text": "Kaydet", "callback_data": "k:1:abcdef12"}]]}
    api.send(1, "x " * 4000, kb)
    sends = [p for m, p in calls if m == "sendMessage"]
    assert len(sends) > 1 and "reply_markup" in sends[-1] and not any("reply_markup" in p for p in sends[:-1])
    api.remove_keyboard(1, 5)
    assert calls[-1][0] == "editMessageReplyMarkup" and calls[-1][1]["reply_markup"] == {"inline_keyboard": []}
