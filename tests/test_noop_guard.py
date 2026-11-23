"""ADR 0015: an event that already exists is never added or edited again; the executor never writes a no-op."""
from test_assistant import NOW, make, say, ev
from sebastian.textutil import similar_title


def test_similar_title_stems():
    assert similar_title("Çöpleri cikaracagim", "Çöpü çıkar")
    assert similar_title("cop cikar", "Çöpü çıkar")
    assert not similar_title("Diş hekimi", "Çöpü çıkar")
    assert not similar_title("", "x")


def test_executor_skips_a_write_when_stored_params_equal_the_current_state():
    e = ev("Çöpü çıkar", 18, 15, 30, 15, reminder_minutes=30)
    a, hub, st, _ = make([e])
    calls = []
    orig = hub.update_event
    hub.update_event = lambda *x, **k: calls.append(x) or orig(*x, **k)
    args = {"event_id": e.event_id, "title": e.title, "before": "x",
            "changes": {"start": e.start.isoformat(), "end": e.end.isoformat(), "all_day": False, "reminder_minutes": 30}, "scope": "this"}
    pid = st.add_pending(a.owner, "edit_event", args, NOW)
    out = say(a, "evet")
    assert calls == [] and "bir şey değiştirmedim" in out
    assert st.get_pending(pid)["status"] == "done" and [r["action"] for r in st.audit_rows()][-1] == "write_noop"


def test_executor_still_writes_a_real_change():
    e = ev("Çöpü çıkar", 18, 15, 30, 15, reminder_minutes=30)
    a, hub, st, _ = make([e])
    st.add_pending(a.owner, "edit_event", {"event_id": e.event_id, "title": e.title, "before": "x",
                                           "changes": {"reminder_minutes": 60}, "scope": "this"}, NOW)
    assert "Değiştirdim" in say(a, "evet") and hub._events[0].reminder_minutes == 60


def test_all_day_duplicate_and_different_time_is_not_a_duplicate():
    a, hub, st, _ = make([ev("Çöpü çıkar", 19, 7, 30, 15)])
    assert "Zaten takvimde" in say(a, "çöpü yarın sabah 7.30'a ekle")
    say(a, "hayır")
    assert "Zaten takvimde" not in say(a, "çöpü yarın sabah 9'a ekle")
