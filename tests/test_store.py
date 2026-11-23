import os
import stat
from datetime import datetime, timedelta
from sebastian.store import Store

NOW = datetime(2026, 11, 18, 10, 0)


def test_file_is_private(tmp_path):
    p = tmp_path / "a" / "sebastian.db"
    Store(p)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_retention_30_days():
    s = Store()
    s.log(1, "old", "in", NOW - timedelta(days=31), 1, True)
    s.log(1, "new", "in", NOW - timedelta(days=29), 2, True)
    assert s.purge(NOW) == 1
    assert [m["text"] for m in s.messages(1)] == ["new"]


def test_update_id_persists(tmp_path):
    s = Store(tmp_path / "d.db")
    assert s.last_update_id() == 0
    s.set_last_update_id(42)
    assert Store(tmp_path / "d.db").last_update_id() == 42


def test_unanswered_and_attempt_limit():
    s = Store()
    a = s.log(1, "hi", "in", NOW, 1)
    b = s.log(1, "done", "in", NOW, 2, answered=True)
    s.log(1, "reply", "out", NOW)
    assert [r["id"] for r in s.unanswered(NOW - timedelta(hours=1))] == [a]
    s.bump_attempt(a); s.bump_attempt(a)
    assert s.unanswered(NOW - timedelta(hours=1)) == []
    assert s.unanswered(NOW + timedelta(hours=1)) == []


def test_pending_hash_ttl_and_supersede():
    s = Store()
    p1 = s.add_pending(1, "add_event", {"x": 1}, NOW)
    p2 = s.add_pending(1, "add_event", {"x": 2}, NOW)
    row = s.open_pending(1)
    assert row["id"] == p2 and Store.pending_state(row, NOW) == "ok"
    assert Store.pending_state(row, NOW + timedelta(minutes=11)) == "expired"
    s.c.execute("UPDATE pending_actions SET args_json='{\"x\": 3}' WHERE id=?", (p2,))
    assert Store.pending_state(s.open_pending(1), NOW) == "tampered"
    assert s.c.execute("SELECT status FROM pending_actions WHERE id=?", (p1,)).fetchone()[0] == "superseded"


def test_nl_counter_and_conversation():
    s = Store()
    s.nl_bump("2026-11-18", 1); s.nl_bump("2026-11-18", 1)
    assert s.nl_calls("2026-11-18", 1) == 2 and s.nl_calls("2026-11-19", 1) == 0
    s.set_conversation(1, {"awaiting": "date"})
    assert s.get_conversation(1) == {"awaiting": "date"}
    s.clear_conversation(1)
    assert s.get_conversation(1) is None
