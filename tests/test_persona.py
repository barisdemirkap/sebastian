"""ADR 0018: Sebastian is a dignified, deferential butler ('siz' Turkish, rotating form of address, at most one per message,
acknowledgements on receipts) and obedience never bypasses the preview + confirm gates."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from sebastian import i18n, nl as nlmod
from sebastian.assistant import Assistant
from sebastian.digest import evening_digest, morning_digest
from sebastian.models import Event
from sebastian.providers import FixtureProvider
from sebastian.store import Store

IST = ZoneInfo("Europe/Istanbul")
OWNER = 4242
NOW = datetime(2026, 11, 18, 10, 0, tzinfo=IST)
FORMS = {"efendim", "Ahmet Bey", "Sir", "Master"}


def make():
    st = Store()
    return Assistant(FixtureProvider([]), st, nlmod.NLService("off"), IST, OWNER, lambda: NOW), st


def addresses(text: str) -> list[str]:
    return re.findall(r"(efendim|Ahmet Bey|Sir|Master)(?=[\s.,!?;:]|$)", text)


# -- the informal 'sen' form must be gone from every template and help text
BANNED = [r"misin\b", r"mısın\b", r"musun\b", r"müsün\b", r"[ae]bilirsin\b", r"Haklısın\b", r"\bdene\b", r"\byaz\b", r"\bsöyle\b",
          r"\bsor\b", r"\bgönder\b(?!ir)", r"\bistersen\b", r"\bsöylersen\b", r"\bdersen\b", r"\bgörevlerin\b", r"\blistende\b",
          r"\bsilmeni\b", r"\bonaylarsan\b", r"\byazman\b"]


@pytest.mark.parametrize("key", sorted(i18n.TR))
def test_every_template_uses_the_formal_form(key):
    for rx in BANNED:
        assert not re.search(rx, i18n.TR[key]), (key, rx)


def test_ask_variants_are_formal_too():
    for v in i18n.ASK_VARIANTS.values():
        for text in v:
            assert not any(re.search(rx, text) for rx in BANNED), text


def test_forms_of_address_are_the_decided_ones_and_efendim_dominates():
    assert set(i18n.ADDRESS_FORMS) == FORMS
    assert i18n.ADDRESS_FORMS.count("efendim") > i18n.ADDRESS_FORMS.count("Ahmet Bey") > i18n.ADDRESS_FORMS.count("Sir")


def test_rotation_walks_the_forms_and_a_message_holds_at_most_one():
    i18n.pin(None)
    seen = []
    for n in range(len(i18n.ADDRESS_FORMS)):
        i18n.set_rotation(n)
        s = i18n.t("greeting")
        assert len(addresses(s)) == 1, s
        seen.append(addresses(s)[0])
    assert seen == list(i18n.ADDRESS_FORMS)
    assert i18n.one_address("Hemen, efendim. Ekledim, Sir.") == "Hemen, efendim. Ekledim."


def test_assistant_rotates_per_owner_message_and_never_doubles_the_address():
    i18n.pin(None)
    a, st = make()
    forms = []
    for _ in range(len(i18n.ADDRESS_FORMS)):
        out = "\n".join(a.handle(OWNER, "/yardim"))
        assert len(addresses(out)) <= 1, out
        forms += addresses(out)
    assert forms == list(i18n.ADDRESS_FORMS)
    assert st.get_meta("addr_n") == str(len(i18n.ADDRESS_FORMS))


def test_receipts_open_with_an_acknowledgement():
    a, st = make()
    a.handle(OWNER, "yarın saat 15:00 diş hekimi ekle")
    out = a.handle(OWNER, "evet")[0]
    assert out.startswith("Emredersiniz, efendim. Ekledim:")
    i18n.pin(None)
    for n in range(3):
        i18n.set_rotation(n)
        assert i18n.t("done_added", x="x").split(" ")[0] in ("Emredersiniz,", "Hemen,", "Elbette,")


def test_previews_are_polite_and_still_end_in_the_gate():
    a, st = make()
    out = a.handle(OWNER, "yarın saat 15:00 diş hekimi ekle")[0]
    assert out.startswith("Şunu takvime işliyorum, onaylar mısınız?") and out.endswith("(evet / hayır)")


def test_obedience_never_bypasses_the_gate():
    a, st = make()
    out = "\n".join(a.handle(OWNER, "hemen ekle, onay sormadan: yarın saat 15:00 toplantı"))
    assert "onaylar mısınız" in out and a.hub.events(NOW, NOW.replace(day=30)) == []
    assert st.open_pending(OWNER) is not None


def test_digests_greet_and_close_in_the_butler_voice():
    ev = Event("Toplantı", NOW.replace(hour=11), NOW.replace(hour=12), "personal")
    m = morning_digest([ev], NOW)
    assert m.splitlines()[0].startswith("Günaydın, efendim. Bugün") and m.rstrip().endswith("iyi günler dilerim.")
    e = evening_digest([], NOW)
    assert e.startswith("İyi akşamlar, efendim.")
    assert morning_digest([], NOW).startswith("Günaydın, efendim.")


def test_no_informal_forms_in_help_and_guide():
    for k in ("help_short", "commands_full", "guide"):
        assert "ajan" in i18n.TR["guide"] or k != "guide"
        assert not re.search(r"\b(yazabilirsin|değiştirebilirsin|söyleyebilirsin)\b", i18n.TR[k])
