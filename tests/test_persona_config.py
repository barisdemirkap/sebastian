"""[persona] config: the owner's form of address and the assistant's name are not hard-coded."""
from sebastian import config as cfg, i18n


def teardown_function():
    i18n.configure()


def test_defaults_are_the_fictional_ones():
    c = cfg.parse({})
    assert (c.owner_title, c.assistant_name) == ("Ahmet Bey", "Sebastian")


def test_persona_section_is_applied_everywhere():
    c = cfg.parse({"persona": {"owner_title": "Dr. Kaya", "assistant_name": "Jeeves"}})
    i18n.configure(c.owner_title, c.assistant_name)
    assert "Dr. Kaya" in i18n.ADDRESS_FORMS and "Ahmet Bey" not in i18n.ADDRESS_FORMS
    assert "Jeeves'sin" in i18n.AGENT_SYSTEM and "Dr. Kaya" in i18n.AGENT_SYSTEM and "Ahmet Bey" not in i18n.AGENT_SYSTEM
    assert i18n._ADDR_RE.search("Tamam, Dr. Kaya.")


def test_blank_values_fall_back_to_defaults():
    i18n.configure("  ", "")
    assert "Ahmet Bey" in i18n.ADDRESS_FORMS and "Sebastian'sin" in i18n.AGENT_SYSTEM
