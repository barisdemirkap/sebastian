"""ADR 0002: the Slack adapter translates to and from the Channel contract (fake Web API, no network)."""
from sebastian.channels import Channel
from sebastian.channels.slack import SlackChannel, blocks_for, split_text

OWNER_CHAT, OWNER_USER = 42, "U111"


def make(events=()):
    calls, queue = [], list(events)
    n = [0]

    def call(method, payload):
        calls.append((method, payload))
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D999"}}
        n[0] += 1
        return {"ok": True, "ts": f"1700000000.{n[0]:06d}"}

    def events_():
        out, queue[:] = list(queue), []
        return out
    return SlackChannel(OWNER_CHAT, OWNER_USER, call, events_, download=lambda url: b"audio"), calls, queue


def dm(text, user=OWNER_USER, ts="1700000001.000100", **extra):
    return {"type": "events_api", "payload": {"event": {"type": "message", "channel_type": "im", "channel": "D999", "user": user, "text": text, "ts": ts, **extra}}}


def tap(data, user=OWNER_USER, mts="1700000000.000001", ats="1700000005.000200"):
    return {"type": "interactive", "payload": {"type": "block_actions", "user": {"id": user}, "trigger_id": "t1", "container": {"message_ts": mts},
                                               "actions": [{"action_id": data, "value": data, "action_ts": ats}]}}


def test_satisfies_the_channel_contract():
    assert isinstance(make()[0], Channel)


def test_owner_dm_becomes_a_normalized_message():
    ch, _, q = make()
    q.append(dm("yarın saat 15:00 diş hekimi ekle"))
    (u,) = ch.get_updates(None)
    m = u["message"]
    assert m["text"].startswith("yarın") and m["chat"] == {"id": OWNER_CHAT, "type": "private"} and m["from"]["id"] == OWNER_CHAT


def test_strangers_bots_edits_and_channels_are_ignored():
    ch, _, q = make()
    q += [dm("hi", user="U222"), dm("hi", subtype="message_changed"), dm("hi", bot_id="B1"),
          {"type": "events_api", "payload": {"event": {"type": "message", "channel_type": "channel", "user": OWNER_USER, "text": "x", "ts": "1.1"}}},
          tap("k:1:abcdef12", user="U222")]
    assert ch.get_updates(None) == []


def test_a_button_tap_becomes_a_callback_query_and_ids_increase():
    ch, _, q = make()
    q += [dm("a", ts="1700000001.000100"), tap("k:1:abcdef12", ats="1700000001.000100")]
    first, second = ch.get_updates(None)
    assert second["callback_query"]["data"] == "k:1:abcdef12" and second["callback_query"]["from"]["id"] == OWNER_CHAT
    assert second["update_id"] > first["update_id"]
    assert ch.get_updates(second["update_id"] + 1) == []


def test_send_opens_the_dm_once_and_renders_buttons_as_blocks():
    ch, calls, _ = make()
    kb = {"inline_keyboard": [[{"text": "Kaydet", "callback_data": "k:1:abcdef12"}, {"text": "İptal", "callback_data": "x:1:abcdef12"}]]}
    mid = ch.send(OWNER_CHAT, "Onaylar mısınız?", kb)
    ch.send(OWNER_CHAT, "başka")
    assert [c[0] for c in calls] == ["conversations.open", "chat.postMessage", "chat.postMessage"] and calls[1][1]["channel"] == "D999"
    actions = calls[1][1]["blocks"][1]["elements"]
    assert [a["action_id"] for a in actions] == ["k:1:abcdef12", "x:1:abcdef12"] and isinstance(mid, int)


def test_edit_and_remove_keyboard_use_chat_update_on_the_right_message():
    ch, calls, _ = make()
    mid = ch.send(OWNER_CHAT, "liste", {"inline_keyboard": [[{"text": "a", "callback_data": "i:1:abcdef12"}]]})
    ch.edit_message(OWNER_CHAT, mid, "liste 2", {"inline_keyboard": [[{"text": "b", "callback_data": "i:2:abcdef12"}]]})
    ch.remove_keyboard(OWNER_CHAT, mid)
    ups = [c[1] for c in calls if c[0] == "chat.update"]
    assert ups[0]["text"] == "liste 2" and ups[0]["ts"] == "1700000000.000001"
    assert ups[1]["text"] == "liste 2" and len(ups[1]["blocks"]) == 1          # the buttons are gone, the text stays


def test_long_text_is_split_and_voice_is_unsupported():
    assert all(len(c) <= 2900 for c in split_text("x" * 7000)) and "".join(split_text("x" * 7000)) == "x" * 7000
    ch, _, _ = make()
    try:
        ch.send_voice(OWNER_CHAT, b"")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError")
    assert blocks_for("t")[0]["text"]["text"] == "t"
