"""Defects found by the conformance suite (ADR 0008): vocabulary gaps, gibberish that cancelled, repeated replies."""
import pytest
from sebastian import dialogue, i18n
from test_assistant import make, say, OWNER, NOW


@pytest.mark.parametrize("text", ["yaz", "tamam yaz", "olur yaz", "kaydetsin", "kaydedelim", "tmm", "evt", "iyi kaydet",
                                  "aynen öyle", "evet kardeş"])
def test_more_ways_to_say_yes(text):
    assert dialogue.parse_confirm(text) == "yes"


@pytest.mark.parametrize("text", ["kaydetme", "kaydetmeyin", "yazma", "hayır kaydetme", "kalsın", "yok kalsın", "hayır kardeş"])
def test_more_ways_to_say_no(text):
    assert dialogue.parse_confirm(text) == "no"


@pytest.mark.parametrize("text", ["hh", "h", "mmmm", "asdf", "e", "eee", "ee"])
def test_gibberish_and_hesitation_are_never_a_cancel_or_a_confirm(text):
    assert dialogue.parse_confirm(text) is None and dialogue.parse_confirm(text, True) is None


def test_a_repeated_reply_gets_a_changing_lead():
    a, hub, st, _ = make()
    say(a, "yarın saat 15:00 diş hekimi ekle")
    seen = []
    for _ in range(5):
        r = a.handle(OWNER, "hmm")
        st.log(OWNER, r[0], "out", NOW)           # the bot loop logs what it sent
        seen.append(r[0])
    assert all(x != y for x, y in zip(seen, seen[1:]))
    assert not seen[0].startswith(i18n.REPEAT_LEADS[0]) and seen[1].startswith(i18n.REPEAT_LEADS[0])
    assert seen[2].startswith(i18n.REPEAT_LEADS[1]) and seen[3].startswith(i18n.REPEAT_LEADS[2])


def test_commands_are_exempt_from_the_repeat_lead():
    a, hub, st, _ = make()
    for _ in range(2):
        r = a.handle(OWNER, "/yardim")
        st.log(OWNER, r[0], "out", NOW)
    assert r[0] == i18n.t("help_short")
