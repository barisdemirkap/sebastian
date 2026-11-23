"""ADR 0006: broad confirmation, and an unrecognised message never destroys a pending preview."""
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sebastian import dialogue
from sebastian.models import EventDraft
from test_assistant import make, say, window, NOW, IST

REOFFER = "Önizleme hâlâ onayınızı bekliyor (evet / hayır)."


@pytest.mark.parametrize("text", ["evet", "Evet.", "EVET!", "evet kaydet", "evet ekle", "tamam", "olur", "onaylıyorum",
                                  "onayliyorum", "Onaylıyorum.", "onay", "kaydet", "ekle", "ok", "okey", "doğru", "yap",
                                  "tabii", "peki", "👍", "Eveet", "tamam kaydet", "peki tamam"])
def test_affirmatives(text):
    assert dialogue.parse_confirm(text) == "yes"


@pytest.mark.parametrize("text", ["hayır", "Hayır!", "yok", "vazgeç", "iptal", "istemiyorum", "dur", "iptal et", "👎",
                                  "boş ver", "vazgeçtim"])
def test_negatives(text):
    assert dialogue.parse_confirm(text) == "no"


@pytest.mark.parametrize("text", ["evet ama saat 16:00 olsun", "tamam ama yarın değil", "emin misin", "evet 16:00",
                                  "tam anlamadım", "hayır saat 16:00 olsun", "", "bugün ne var"])
def test_not_plain_confirmations(text):
    assert dialogue.parse_confirm(text) is None


def test_edit_split():
    assert dialogue.split_edit("evet ama saati 16:00 yap") == "saati 16:00 yap"
    assert dialogue.split_edit("tamam ama yarın olsun") == "yarın olsun"
    assert dialogue.split_edit("evet") is None


@pytest.mark.parametrize("text,want", [("evet", "weak"), ("tamam", "weak"), ("👍", "weak"), ("evet sil", "yes"), ("sil", "yes"),
                                       ("onaylıyorum", "yes"), ("Evet, sil.", "yes"), ("hayır", "no")])
def test_delete_is_stricter(text, want):
    assert dialogue.parse_confirm(text, delete=True) == want


def import_preview(a, st):
    drafts = [EventDraft("Toplantı A", datetime(2026, 11, 19, 10, tzinfo=IST), datetime(2026, 11, 19, 11, tzinfo=IST)),
              EventDraft("Toplantı B", datetime(2026, 11, 20, 10, tzinfo=IST), datetime(2026, 11, 20, 11, tzinfo=IST))]
    return a.propose_import(drafts, NOW)


def test_live_sequence_onayliyorum_saves():
    a, hub, st, _ = make()
    import_preview(a, st)
    assert say(a, "emin misin") == "Tam anlamadım. " + REOFFER
    assert say(a, "onayliyorum") == "Emredersiniz, efendim. 2 etkinlik ekledim."
    assert len(window(hub)) == 2


def test_unclear_text_keeps_preview_then_evet_saves():
    a, hub, st, _ = make()
    import_preview(a, st)
    assert say(a, "tam anlamadım") == "Tam anlamadım. " + REOFFER
    assert st.open_pending(1000000001) is not None
    assert say(a, "evet") == "Emredersiniz, efendim. 2 etkinlik ekledim."


def test_chitchat_and_read_keep_preview():
    a, hub, st, _ = make()
    import_preview(a, st)
    assert say(a, "merhaba").endswith(REOFFER)
    assert say(a, "bugün ne var").endswith(REOFFER)
    assert say(a, "tamam") == "Emredersiniz, efendim. 2 etkinlik ekledim."


def test_evet_ama_never_confirms():
    a, hub, st, _ = make()
    say(a, "yarın saat 15:00 diş hekimi ekle")
    r = say(a, "evet ama saat 16:00 olsun")          # an edit: shown again, never saved
    assert r.startswith("Önizlemeyi güncelledim.") and "16:00-17:00" in r
    assert window(hub) == []
    assert say(a, "evet").startswith("Emredersiniz, efendim. Ekledim: Diş hekimi, Perşembe 19 Kasım 2026 16:00-17:00")
    say(a, "yarın saat 15:00 berber ekle")
    assert say(a, "evet ama bilmiyorum").endswith(REOFFER)       # edit text without content: keep the preview


def test_new_request_needs_parking_consent():
    a, hub, st, _ = make()
    say(a, "yarın saat 15:00 diş hekimi ekle")
    assert say(a, "cuma saat 10:00 berber ekle") == "Önceki önizleme iptal edilsin mi? (evet / hayır)"
    assert say(a, "hayır") == "Pekâlâ, efendim, önceki önizleme duruyor. (evet / hayır)"
    assert say(a, "evet").startswith("Emredersiniz, efendim. Ekledim: Diş hekimi")


def test_explicit_cancel_and_expiry_close_it():
    a, hub, st, clock = make()
    say(a, "yarın saat 15:00 diş hekimi ekle")
    assert say(a, "vazgeç").startswith("Pekâlâ, efendim, vazgeçtim")
    assert st.open_pending(1000000001) is None
    say(a, "yarın saat 15:00 diş hekimi ekle")
    clock.now = NOW + timedelta(minutes=11)
    assert "ne var" not in say(a, "bugün ne var")          # expired: handled as a fresh request
    assert st.open_pending(1000000001) is None


def test_delete_needs_explicit_word():
    from sebastian.models import Event
    from test_assistant import ev
    a, hub, st, _ = make([ev("Diş hekimi", 19, 15, 0)])
    say(a, "diş hekimini sil")
    assert say(a, "tamam").startswith('Silmem için "evet sil"')
    assert say(a, "bir şey") .endswith("(evet sil / hayır).")
    assert say(a, "onaylıyorum").startswith("Emredersiniz, efendim. Sildim")
    assert window(hub) == []


def test_without_pending_evet_is_nothing_pending_and_tamam_is_chitchat():
    a, *_ = make()
    assert say(a, "evet") == "Onay bekleyen bir işlem yok, hiçbir şey yazmadım."
    assert say(a, "onaylıyorum").startswith("Tam anlayamadım")


def test_help_mentions_correcting_a_preview():
    a, *_ = make()
    assert 'Önizlemeyi düzeltebilirsiniz: "saati 16:00 yap".' in say(a, "/yardim")
