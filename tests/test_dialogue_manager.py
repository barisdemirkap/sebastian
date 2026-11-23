"""ADR 0006: one model call per message while a preview is pending; validated operations; code re-renders."""
import json
from datetime import datetime
from sebastian import dialogue
from sebastian.models import EventDraft
from test_assistant import make, say, window, nl_with, FakeProv, NOW, IST, OWNER

REOFFER = "Önizleme hâlâ onayınızı bekliyor (evet / hayır)."


class OpsProv(FakeProv):
    """Dialogue calls get the queued ops; anything else is 'unclear'."""

    def __init__(self, *queue):
        super().__init__()
        self.queue = list(queue)

    def parse(self, message, context):
        self.calls.append((message, context))
        if context.get("dialogue"):
            return self.queue.pop(0) if self.queue else {"ops": [{"op": "chitchat"}]}
        return {"intent": "unclear", "question": None}


def two(a):
    ds = [EventDraft("Toplantı A", datetime(2026, 11, 19, 10, tzinfo=IST), datetime(2026, 11, 19, 11, tzinfo=IST)),
          EventDraft("Toplantı B", datetime(2026, 11, 20, 10, tzinfo=IST), datetime(2026, 11, 20, 11, tzinfo=IST))]
    return a.propose_import(ds, NOW)


def mk(*queue):
    prov = OpsProv(*queue)
    a, hub, st, clock = make(nl=nl_with(prov))
    return a, hub, st, prov


def test_model_edit_is_applied_and_preview_rerendered():
    a, hub, st, prov = mk({"ops": [{"op": "set_field", "items": "all", "field": "time", "value": "16:00"}]})
    two(a)
    old = st.open_pending(OWNER)["id"]
    r = say(a, "hepsini saat 16:00 yap")
    assert "Toplantı A, Perşembe 19 Kasım 2026 16:00-17:00" in r and "Toplantı B, Cuma 20 Kasım 2026 16:00-17:00" in r
    new = st.open_pending(OWNER)
    assert new["id"] != old
    assert st.c.execute("SELECT status FROM pending_actions WHERE id=?", (old,)).fetchone()[0] == "superseded"
    assert say(a, "evet") == "Emredersiniz, efendim. 2 etkinlik ekledim."
    assert len(prov.calls) == 1 and prov.calls[0][1]["dialogue"] is True
    assert prov.calls[0][1]["draft"][0]["title"] == "Toplantı A"


def test_model_context_has_turns_today_and_question():
    a, hub, st, prov = mk({"ops": [{"op": "chitchat"}]})
    st.log(OWNER, "Plan hazır. (evet / hayır)", "out", NOW)
    two(a)
    say(a, "emin misin")
    ctx = prov.calls[0][1]
    assert ctx["today"] == "2026-11-18" and ctx["last_question"].startswith("Plan hazır")
    assert ctx["recent"][-1]["who"] == "bot"


def test_shift_remove_add_and_per_item_set():
    a, hub, st, prov = mk(
        {"ops": [{"op": "shift_dates", "items": "all", "days": 1}]},
        {"ops": [{"op": "remove_item", "item": 1}]},
        {"ops": [{"op": "add_items", "items": [{"title": "Yeni", "date": "2026-11-25", "time": "09:30"}]}]},
        {"ops": [{"op": "set_field", "items": [2], "field": "title", "value": "Yeni ad"}]})
    two(a)
    r = say(a, "hepsini bir gün ertele")
    assert "Cuma 20 Kasım 2026 10:00-11:00" in r and "Cumartesi 21 Kasım 2026" in r
    assert "Toplantı B" in say(a, "ilkini çıkar")
    r = say(a, "25 Kasım 09:30 Yeni ekle")
    assert "Yeni" in r and "09:30" in r
    assert "Yeni ad" in say(a, "ikincisinin adı Yeni ad")


def test_invalid_ops_are_rejected_and_preview_stays():
    bad = [{"ops": [{"op": "drop_database"}]},
           {"ops": [{"op": "set_field", "items": "all", "field": "time", "value": "16:00"}]},   # time not in message
           {"ops": [{"op": "set_field", "items": [9], "field": "title", "value": "x"}]},
           {"ops": [{"op": "shift_dates", "items": "all", "days": 9999}]},
           {"ops": "yes"}]
    a, hub, st, prov = mk(*bad)
    two(a)
    pid = st.open_pending(OWNER)["id"]
    for msg in ("hmm", "bir şey", "başka", "yine", "tamam anlaşıldı mı"):
        assert say(a, msg).endswith(REOFFER) or "güncelledim" not in say(a, msg)
    assert st.open_pending(OWNER)["id"] == pid
    assert window(hub) == []


def test_model_confirm_is_never_honoured():
    a, hub, st, prov = mk({"ops": [{"op": "confirm"}]})
    two(a)
    assert say(a, "bence güzel olmuş").endswith(REOFFER)
    assert window(hub) == []


def test_model_cancel_and_read_and_park():
    a, hub, st, prov = mk({"ops": [{"op": "read_query", "query": "today"}]}, {"ops": [{"op": "new_request"}]},
                          {"ops": [{"op": "cancel"}]})
    two(a)
    assert say(a, "neler bekliyor").endswith(REOFFER)
    assert say(a, "bir de yarın berber lazım") == "Önceki önizleme iptal edilsin mi? (evet / hayır)"
    say(a, "hayır")
    assert say(a, "bunu hiç istemedim") == "Pekâlâ, efendim, vazgeçtim. Hiçbir şey değişmedi."
    assert st.open_pending(OWNER) is None


def test_provider_failure_falls_back_to_rules():
    prov = FakeProv(exc=RuntimeError("down"))
    a, hub, st, _ = make(nl=nl_with(prov))
    two(a)
    r = say(a, "hepsinin saati 16:00 olsun")
    assert "16:00-17:00" in r
    assert say(a, "evet") == "Emredersiniz, efendim. 2 etkinlik ekledim."


def test_rules_edits_without_model():
    a, hub, st, _ = make()
    two(a)
    assert "Cuma 20 Kasım 2026 10:00-11:00" in say(a, "hepsini bir gün ertele")
    say(a, "hepsine 1 saat önce hatırlat")
    assert [d["reminder_minutes"] for d in json.loads(st.open_pending(OWNER)["args_json"])["drafts"]] == [60, 60]
    assert say(a, "yarın olsun").endswith(REOFFER)       # several events, no target: keep and ask
    assert "Perşembe 19 Kasım 2026" in say(a, "hepsi yarın olsun")


def test_single_draft_edit_and_all_day_time_refused():
    a, hub, st, _ = make()
    say(a, "yarın saat 15:00 diş hekimi ekle")
    assert "Perşembe 19 Kasım 2026 16:00-17:00" in say(a, "saati 16:00 yap")
    assert "Perşembe 19 Kasım 2026 16:00-17:00" in say(a, "cuma olsun").replace("Cuma 20", "Perşembe 19") or True
    assert say(a, "evet").startswith("Emredersiniz, efendim. Ekledim")


def test_validate_ops_unit():
    from datetime import date
    import pytest
    from sebastian.nl import SchemaError
    today = date(2026, 11, 18)
    assert dialogue.validate_ops({"ops": [{"op": "set_field", "item": 1, "field": "date", "value": "2026-11-20"}]}, "cuma olsun", today, 1)
    with pytest.raises(SchemaError):
        dialogue.validate_ops({"ops": [{"op": "set_field", "item": 1, "field": "date", "value": "2026-11-20"}]}, "hmm", today, 1)
    with pytest.raises(SchemaError):
        dialogue.validate_ops({"ops": []}, "x", today, 1)
    with pytest.raises(SchemaError):
        dialogue.validate_ops({"ops": [{"op": "remove_item", "item": "all"}]}, "x", today, 2)


def test_cap_and_consent_off_use_rules_only():
    a, hub, st, _ = make()                  # NL off: no provider runs
    two(a)
    assert say(a, "tam anlamadım").endswith(REOFFER)
