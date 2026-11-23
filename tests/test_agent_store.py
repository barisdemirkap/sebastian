"""ADR 0019: agent tables are added idempotently; an existing database is backed up first (once)."""
import os
import sqlite3
from datetime import datetime

from sebastian.store import Store

NOW = datetime(2026, 11, 19, 10, 0)


def test_database_with_lists_but_no_agent_tables_is_backed_up_once(tmp_path):
    p = tmp_path / "a.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE list_items (id INTEGER PRIMARY KEY, chat_id INTEGER, list_key TEXT, text TEXT, status TEXT, created_at TEXT, closed_at TEXT)")
    c.execute("INSERT INTO list_items VALUES (1, 1, 'alinacaklar', 'Süt', 'open', 'x', NULL)")
    c.commit()
    c.close()
    st = Store(p)
    bak = tmp_path / "a.db.pre-agent.bak"
    assert bak.exists() and oct(os.stat(bak).st_mode & 0o777) == "0o600"
    assert "agent_grants" not in {r[0] for r in sqlite3.connect(bak).execute("SELECT name FROM sqlite_master")}
    assert [r["text"] for r in st.list_items(1, "alinacaklar")] == ["Süt"]
    st.c.close()
    m = bak.stat().st_mtime_ns
    Store(p)
    assert bak.stat().st_mtime_ns == m


def test_grants_are_single_use_per_exact_version_and_one_live_at_a_time():
    st = Store()
    st.grant_mint(1, 5, "h5", NOW)
    assert not st.grant_consume(1, 5, "other") and not st.grant_consume(1, 6, "h5")
    assert st.grant_consume(1, 5, "h5") and not st.grant_consume(1, 5, "h5")
    st.grant_mint(1, 7, "h7", NOW)
    st.grant_mint(1, 8, "h8", NOW)
    assert not st.grant_consume(1, 7, "h7") and st.grant_live(1)["pending_id"] == 8
    st.grant_revoke_all(1)
    assert st.grant_live(1) is None and not st.grant_consume(1, 8, "h8")


def test_notes_and_sessions_round_trip_per_chat():
    st = Store()
    st.note_set(1, "k", "v1", NOW)
    st.note_set(1, "k", "v2", NOW)
    st.note_set(2, "k", "other", NOW)
    assert [(r["key"], r["value"]) for r in st.notes(1)] == [("k", "v2")]
    st.agent_start_session(1, "sid", "pv", NOW)
    st.agent_touch_session(1, NOW)
    assert st.agent_session(1)["turns"] == 1 and st.agent_session(2) is None
    st.agent_drop_session(1)
    assert st.agent_session(1) is None
