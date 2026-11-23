"""Runs the bot-agnostic conformance suite (invariants.py) against Sebastian. Fast and deterministic (fixed seeds)."""
import pytest

import invariants as inv
import phrasebook_tr as pb
from sebastian_adapter import make_adapter

# (setup, messages that open a preview)
SINGLE = ({"provider": "rules"}, ["yarın saat 15:00 diş hekimi ekle"])
BULK = ({"provider": "rules", "import": 3}, [])
MODEL = ({"provider": "claude_like", "import": 2}, [])
EDIT = ({"provider": "rules", "seed": [["Demo toplantı", 14]]}, ["demo toplantıyı 16:00'ya ertele"])
DELETE = ({"provider": "rules", "seed": [["Demo toplantı", 14]]}, ["demo toplantıyı sil"])
SETUPS = {"single": SINGLE, "bulk": BULK, "model": MODEL, "edit": EDIT, "delete": DELETE}


def every(items, n):
    """A deterministic subset (every n-th) for the secondary previews; the main preview gets the full list."""
    return items[::n]


def _id(s):
    return s[:20]


def test_phrasebook_is_big_enough():
    assert len(pb.AFFIRMATIONS) >= 60 and len(pb.NEGATIONS) >= 30


CASES = [("single", p) for p in pb.AFFIRMATIONS] + [(n, p) for n in SETUPS if n != "single" for p in every(pb.AFFIRMATIONS, 7)]
NEG_CASES = [(n, p) for n in ("single", "bulk") for p in pb.NEGATIONS] + \
    [(n, p) for n in SETUPS if n not in ("single", "bulk") for p in every(pb.NEGATIONS, 5)]


@pytest.mark.parametrize("name,phrase", CASES, ids=lambda x: _id(x))
def test_affirmation_saves_exactly_the_preview(name, phrase):
    setup, opening = SETUPS[name]
    inv.check_confirmation(make_adapter, setup, opening, phrase, "save")


@pytest.mark.parametrize("name,phrase", NEG_CASES, ids=lambda x: _id(x))
def test_negation_cancels(name, phrase):
    setup, opening = SETUPS[name]
    inv.check_confirmation(make_adapter, setup, opening, phrase, "cancel")


@pytest.mark.parametrize("name", ["single", "bulk", "model"])
@pytest.mark.parametrize("phrase", pb.EDITS, ids=_id)
def test_edit_never_saves(name, phrase):
    setup, opening = SETUPS[name]
    inv.check_confirmation(make_adapter, setup, opening, phrase, "edit")


@pytest.mark.parametrize("name", SETUPS)
@pytest.mark.parametrize("phrase", pb.NEUTRAL, ids=_id)
def test_neutral_message_keeps_the_preview(name, phrase):
    setup, opening = SETUPS[name]
    inv.check_confirmation(make_adapter, setup, opening, phrase, "keep")


@pytest.mark.parametrize("name", SETUPS)
def test_repeated_neutral_messages_never_repeat_the_bot(name):
    setup, opening = SETUPS[name]
    conv = inv.Conversation(make_adapter(setup))
    for m in opening:
        conv.say(conv.a.owner, m, kind="free")
    for _ in range(3):
        for junk in ("asdf", "hmm", "😀", "asdf"):
            conv.say(conv.a.owner, junk)
    assert conv.a.preview(conv.a.owner) is not None


@pytest.mark.parametrize("name,script", inv.load_incidents(), ids=lambda x: x if isinstance(x, str) else "")
def test_incident(name, script):
    inv.run_script(make_adapter, script)


def test_incident_folder_is_not_empty():
    assert len(inv.load_incidents()) >= 6


def test_health_menu_appears_once_and_preview_survives():
    """ADR 0009: after S4 (frustration) a way-out menu appears once (cooldown); the invariants still hold, preview stays."""
    from sebastian.i18n import t
    conv = inv.Conversation(make_adapter({"provider": "rules"}))
    me = conv.a.owner
    conv.say(me, "yarın saat 15:00 diş hekimi ekle", kind="free")
    first = conv.say(me, "ulan salak", kind="neutral")
    assert first.count(t("health_menu")) == 1
    second = conv.say(me, "aptal", kind="neutral")
    assert t("health_menu") not in second
    assert conv.a.preview(me) is not None


def test_unclear_run_menu_keeps_preview():
    from sebastian.i18n import t
    conv = inv.Conversation(make_adapter({"provider": "rules"}))
    me = conv.a.owner
    conv.say(me, "yarın saat 15:00 diş hekimi ekle", kind="free")
    menus = sum(conv.say(me, j, kind="neutral").count(t("health_menu")) for j in ("qqq", "www", "eee", "rrr"))
    assert menus <= 1
    assert conv.a.preview(me) is not None


@pytest.mark.parametrize("provider", ["rules", "fail", "claude_like"])
def test_every_reply_of_a_long_mixed_conversation_is_clean(provider):
    """I1/I8 over a long scripted mix (fixed order, no randomness), also with a failing and a working model."""
    conv = inv.Conversation(make_adapter({"provider": provider, "import": 1}))
    me = conv.a.owner
    for text in pb.NEUTRAL * 2:
        conv.say(me, text, kind="neutral")
    conv.say(me, "evet", kind="confirm")
    assert len(conv.a.writes()) == 1
