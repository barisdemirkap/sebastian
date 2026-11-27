"""Regression: a custom [persona] owner_title is the only form of address the replies use. The English pack used to
hard-code "sir" in its rotation and in one template, so `--title "Ms. Doe"` was still called "sir". Offline."""
import re

import pytest

from test_assistant import make, say
from sebastian import i18n, languages

FORBIDDEN = {"en": r"\b(sir|madam|Master|efendim)\b", "tr": r"\b(Sir|Master)\b"}
DEFAULT = {"en": "Mr. Smith", "tr": "Ahmet Bey"}


@pytest.mark.parametrize("code", ["en", "tr"])
def test_custom_title_replaces_every_form_in_the_rotation(code):
    i18n.configure("Ms. Doe", "Sebastian", code)
    assert "Ms. Doe" in i18n.ADDRESS_FORMS
    assert not any(re.search(FORBIDDEN[code], f) for f in i18n.ADDRESS_FORMS if f != "efendim"), i18n.ADDRESS_FORMS
    if code == "en":
        assert set(i18n.ADDRESS_FORMS) == {"Ms. Doe"}


@pytest.mark.parametrize("code", ["en", "tr"])
def test_default_title_keeps_the_pack_rotation(code):
    i18n.configure(None, "Sebastian", code)
    assert DEFAULT[code] in i18n.ADDRESS_FORMS
    if code == "tr":
        assert i18n.ADDRESS_FORMS.count("Sir") == 1 and "Master" in i18n.ADDRESS_FORMS     # Turkish default behaviour is unchanged


@pytest.mark.parametrize("code", ["en", "tr"])
def test_every_rotation_step_of_a_reply_uses_the_custom_title(code):
    i18n.configure("Ms. Doe", "Sebastian", code)
    i18n.pin(None)
    for n in range(24):
        i18n.set_rotation(n)
        text = i18n.t("cancelled") + " " + i18n.t("agent_not_saved") + " " + i18n.t("readonly_mode")
        assert not re.search(FORBIDDEN[code], text), (n, text)


def test_english_retry_intro_has_no_hard_coded_sir():
    assert "sir" not in languages.get("en").messages["retry_intro"].lower()


def test_english_assistant_replies_say_the_title_not_sir():
    a, hub, st, clock = make()
    i18n.configure("Ms. Doe", "Sebastian", "en")
    i18n.pin(None)
    for n in range(10):
        i18n.set_rotation(n)
        out = say(a, "/cancel") + say(a, "/help")
        assert not re.search(r"\bsir\b", out, re.I), out
    i18n.set_rotation(0)
    assert "Ms. Doe" in say(a, "/cancel")


def test_agent_prompt_names_the_title_for_english():
    i18n.configure("Ms. Doe", "Sebastian", "en")
    assert "Ms. Doe" in i18n.AGENT_SYSTEM and "{owner_title}" not in i18n.AGENT_SYSTEM


def test_model_text_with_sir_is_still_limited_to_one_form_of_address():
    i18n.configure("Ms. Doe", "Sebastian", "en")
    assert i18n.one_address("Done, Ms. Doe. Added, sir.") == "Done, Ms. Doe. Added."
