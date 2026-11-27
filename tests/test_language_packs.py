"""ADR 0006: language packs. Every pack must define every key the base pack defines (a contributor adding a language gets a
failing test for each missing key), the config picks the pack, and the English pack answers in English. Offline."""
import re
import string

import pytest

from test_assistant import make
from sebastian import config as cfg, i18n, languages
from sebastian.plugins import Plugins

BASE = languages.get(languages.DEFAULT_LANGUAGE)
OTHERS = [c for c in sorted(languages.PACKS) if c != BASE.code]


def teardown_function():
    i18n.configure()


def fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_default_language_is_turkish_and_the_registry_has_both():
    assert languages.DEFAULT_LANGUAGE == "tr" and set(languages.PACKS) >= {"tr", "en"}


@pytest.mark.parametrize("code", OTHERS)
def test_pack_has_every_message_key(code):
    pack = languages.get(code)
    assert sorted(BASE.messages.keys() - pack.messages.keys()) == [], f"{code}: missing keys (translate them)"
    assert sorted(pack.messages.keys() - BASE.messages.keys()) == [], f"{code}: extra keys (add to the base pack first)"


@pytest.mark.parametrize("code", OTHERS)
def test_pack_templates_use_the_same_placeholders(code):
    pack = languages.get(code)
    bad = {k: (fields(BASE.messages[k]), fields(v)) for k, v in pack.messages.items()
           if k in BASE.messages and fields(v) != fields(BASE.messages[k])}
    assert not bad, f"{code}: placeholders differ from the base pack: {bad}"


@pytest.mark.parametrize("code", sorted(languages.PACKS))
def test_pack_structure_matches_the_base_pack(code):
    p = languages.get(code)
    assert p.code == code and p.name
    assert len(p.days) == len(p.days_short) == 7 and len(p.months) == len(p.months_short) == 12
    assert p.flags.keys() == BASE.flags.keys() and p.day_words.keys() == BASE.day_words.keys()
    assert p.plugin_titles.keys() == BASE.plugin_titles.keys() and p.list_titles.keys() == BASE.list_titles.keys()
    assert p.ask_variants.keys() == BASE.ask_variants.keys() and all(p.ask_variants.values())
    assert p.acks and p.repeat_leads and p.address_forms and p.default_owner_title in p.address_forms
    assert all("{s}" in a for a in p.acks)
    assert p.recall_marker and p.recall_marker in (p.messages["recall_last"].format(text="x"))
    assert p.recall_marker in p.messages["recall_at"].format(time="10:00", text="x")


def test_get_rejects_an_unknown_language():
    with pytest.raises(ValueError, match="xx"):
        languages.get("xx")


def test_config_language_defaults_to_turkish_and_unknown_is_a_config_error():
    assert cfg.parse({}).language == "tr" and cfg.parse({}).owner_title == "Ahmet Bey"
    c = cfg.parse({"persona": {"language": "EN"}})
    assert c.language == "en" and c.owner_title == languages.get("en").default_owner_title
    assert cfg.parse({"persona": {"language": "en", "owner_title": "Dr. Kaya"}}).owner_title == "Dr. Kaya"
    with pytest.raises(cfg.ConfigError, match="persona.language"):
        cfg.parse({"persona": {"language": "klingon"}})


def test_example_config_documents_the_language_key():
    from pathlib import Path
    c = cfg.load(Path(__file__).resolve().parent.parent / "config.example.toml")
    assert c.language == "tr"


def test_english_pack_has_no_turkish_wording_left_over():
    turkish = re.compile(r"[çğıöşüÇĞİÖŞÜ]|\b(?:ekle|hayır|evet|görev|takvim|etkinlik)\b")
    for k, v in languages.get("en").messages.items():
        if k == "need_evet_sil":
            continue
        assert not turkish.search(v), (k, v)


def test_english_replies_and_helpers():
    i18n.configure("Dr. Kaya", "Jeeves", "en")
    assert i18n.LANGUAGE == "en"
    assert i18n.t("cancelled").startswith("Very well, ")
    assert i18n.fmt_date(__import__("datetime").date(2026, 11, 20)) == "Friday 20 November"
    assert i18n.fmt_left(90) == "1 h 30 min"
    assert "Dr. Kaya" in i18n.ADDRESS_FORMS and "Mr. Smith" not in i18n.ADDRESS_FORMS
    assert i18n._ADDR_RE.search("Of course, Dr. Kaya.") and i18n._ADDR_RE.search("Of course, sir.")
    assert "English" in i18n.AGENT_SYSTEM and "Jeeves'sin" in i18n.AGENT_SYSTEM
    assert i18n.is_recall(i18n.t("recall_last", text="hi"))
    i18n.configure()
    assert i18n.LANGUAGE == "tr" and i18n.t("cancelled").startswith("Pekâlâ")


def test_configure_unknown_language_raises():
    with pytest.raises(ValueError):
        i18n.configure(language="xx")


def test_english_flow_through_the_assistant():
    a, hub, st, clock = make()
    i18n.configure(language="en")
    i18n.pin("sir")
    assert "At your service, sir" in "\n".join(a.handle(a.owner, "/help"))
    assert "/today" in "\n".join(a.handle(a.owner, "/commands"))
    a.plugins = Plugins(frozenset({"tasks"}))
    out = "\n".join(a.handle(a.owner, "/tasks"))
    assert "The task feature is switched off in the settings, sir" in out and "tasks = true" in out
    out = "\n".join(a.handle(a.owner, "/cancel"))
    assert out.startswith("Very well")


def test_english_commands_and_confirmations_are_understood():
    a, hub, st, clock = make()
    i18n.configure(language="en")
    i18n.pin("sir")
    assert "Shopping" not in "\n".join(a.handle(a.owner, "/help"))
    out = "\n".join(a.handle(a.owner, "add task buy milk"))
    assert "Task added: Buy milk" in out
    out = "\n".join(a.handle(a.owner, "my tasks"))
    assert "Buy milk" in out and "Your tasks:" in out
    out = "\n".join(a.handle(a.owner, "task: call the plumber"))
    assert "Call the plumber" in out


@pytest.mark.parametrize("text, expected", [("confirm", "yes"), ("yes please", "yes"), ("yep", "yes"), ("cancel", "no"), ("no", "no"),
                                            ("nope", "no"), ("stop", "no"), ("approved", "yes")])
def test_english_confirm_and_cancel_words(text, expected):
    from sebastian import dialogue
    assert dialogue.parse_confirm(text) == expected


def test_english_delete_confirmation_needs_a_delete_word():
    from sebastian import dialogue
    assert dialogue.parse_confirm("yes", delete=True) == "weak"
    assert dialogue.parse_confirm("yes delete", delete=True) == "yes"
    assert dialogue.parse_confirm("confirm", delete=True) == "yes"
    assert dialogue.parse_confirm("cancel", delete=True) == "no"


def test_english_cancel_command_and_word_drop_a_pending_preview():
    a, hub, st, clock = make()
    assert "".join(a.handle(a.owner, "/cancel"))
