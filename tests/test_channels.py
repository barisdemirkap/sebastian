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
