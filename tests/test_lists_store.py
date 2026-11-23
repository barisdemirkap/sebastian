"""ADR 0016: list storage, idempotent migration with a pre-migration backup, per-owner isolation."""
import os
import sqlite3
from datetime import datetime
from sebastian.store import Store

NOW = datetime(2026, 11, 19, 10, 0)


def test_items_are_per_owner_and_per_list():
    st = Store()
    a = st.add_list_item(1, "alinacaklar", "Süt", NOW)
    st.add_list_item(2, "alinacaklar", "Kahve", NOW)
    st.add_list_item(1, "baska", "X", NOW)
    assert [r["text"] for r in st.list_items(1, "alinacaklar")] == ["Süt"]
    assert st.close_list_items([a], "done", NOW) == 1 and st.close_list_items([a], "removed", NOW) == 0
    assert [r["text"] for r in st.list_items(1, "alinacaklar", "done")] == ["Süt"] and st.list_items(1, "alinacaklar") == []


def test_old_database_is_backed_up_once_then_migrated_idempotently(tmp_path):
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, chat_id INTEGER, title TEXT, status TEXT, created_at TEXT, closed_at TEXT)")
    c.execute("INSERT INTO tasks VALUES (1, 1, 'eski görev', 'open', 'x', NULL)")
    c.commit()
    c.close()
    st = Store(p)
    bak = tmp_path / "old.db.pre-lists.bak"
    assert bak.exists() and oct(os.stat(bak).st_mode & 0o777) == "0o600"
    assert sqlite3.connect(bak).execute("SELECT title FROM tasks").fetchall() == [("eski görev",)]
    assert "list_items" not in {r[0] for r in sqlite3.connect(bak).execute("SELECT name FROM sqlite_master")}
    assert st.open_tasks(1)[0]["title"] == "eski görev" and st.get_meta("schema_version") == "4"
    st.add_list_item(1, "alinacaklar", "Süt", NOW)
    st.c.close()
    mtime = bak.stat().st_mtime_ns
    st2 = Store(p)                                   # second open: no new backup, data intact
    assert bak.stat().st_mtime_ns == mtime and [r["text"] for r in st2.list_items(1, "alinacaklar")] == ["Süt"]


def test_fresh_database_gets_no_backup(tmp_path):
    Store(tmp_path / "new.db")
    assert not list(tmp_path.glob("*.bak"))
