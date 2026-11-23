"""SQLite state for the bot (stdlib only): message log with 30-day retention, last update_id, pending actions with
an args hash and TTL, audit log, natural-language usage counter, per-chat conversation. File mode 0600."""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

RETENTION_DAYS = 30
PENDING_TTL = timedelta(minutes=10)
SCHEMA_VERSION = 4   # 2: named lists (list_items); 3: agent mode (sessions, notes, grants); 4: retry queue (ADR 0021)
MAX_ATTEMPTS = 2  # a message that crashes the handler is retried once after restart, then dropped

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, update_id INTEGER,
    direction TEXT NOT NULL CHECK (direction IN ('in','out')), text TEXT NOT NULL, at TEXT NOT NULL,
    answered INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, voice INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pending_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, intent TEXT NOT NULL, args_json TEXT NOT NULL,
    args_hash TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL, chat_id INTEGER
);
CREATE TABLE IF NOT EXISTS conversation (chat_id INTEGER PRIMARY KEY, state_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nl_usage (day TEXT NOT NULL, chat_id INTEGER NOT NULL, calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, chat_id));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS health_events (  -- ADR 0009: signal names only, never message text
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, at TEXT NOT NULL, signal TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tasks (  -- ADR 0013: the owner's to-dos (Sebastian's own list, not calendar events)
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL, closed_at TEXT
);
CREATE TABLE IF NOT EXISTS event_done (  -- training loop: calendar events the owner said are finished (closed here, never deleted from the calendar)
    chat_id INTEGER NOT NULL, event_id TEXT NOT NULL, title TEXT NOT NULL, closed_at TEXT NOT NULL, PRIMARY KEY (chat_id, event_id)
);
CREATE TABLE IF NOT EXISTS list_items (  -- ADR 0016: named lists per owner; status open | done | removed
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, list_key TEXT NOT NULL, text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, closed_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_sessions (  -- ADR 0019: the persistent model session of the owner chat
    chat_id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, prompt_version TEXT NOT NULL, turns INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL, last_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_notes (  -- facts and preferences that survive session rotation
    chat_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (chat_id, key)
);
CREATE TABLE IF NOT EXISTS agent_grants (  -- single-use grants minted by CODE for one exact draft version
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, pending_id INTEGER NOT NULL, args_hash TEXT NOT NULL,
    created_at TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS retry_queue (  -- ADR 0021: messages kept while the language model was unavailable; status queued | done | cleared
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, text TEXT NOT NULL, queued_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued', closed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_health_at ON health_events(at);
"""


def args_hash(intent: str, args: dict) -> str:
    return hashlib.sha256((intent + json.dumps(args, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        path = str(path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            os.close(fd)
            os.chmod(path, 0o600)
        self.c = sqlite3.connect(path, check_same_thread=False)    # the agent bridge thread serves tools while the bot waits
        self.c.row_factory = sqlite3.Row
        self._backup_before_migration(path)
        self.c.executescript(SCHEMA)          # idempotent: every statement is IF NOT EXISTS
        if "due" not in {r[1] for r in self.c.execute("PRAGMA table_info(tasks)")}:
            self.c.execute("ALTER TABLE tasks ADD COLUMN due TEXT")      # the day a task is meant for (None: no day)
        self.c.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        self.c.commit()

    def _backup_before_migration(self, path: str) -> None:
        """An existing database that lacks the newest table is copied (consistent SQLite backup, mode 0600) next to
        itself BEFORE the schema is extended; a fresh or already migrated database is left alone (ADR 0016)."""
        if path == ":memory:":
            return
        tables = {r[0] for r in self.c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables or "event_done" in tables:
            return
        bak = (f"{path}.pre-lists.bak" if "list_items" not in tables else
               f"{path}.pre-agent.bak" if "agent_grants" not in tables else
               f"{path}.pre-retry.bak" if "retry_queue" not in tables else f"{path}.pre-statements.bak")
        if os.path.exists(bak):
            return
        fd = os.open(bak, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        dest = sqlite3.connect(bak)
        try:
            self.c.backup(dest)
        finally:
            dest.close()
        os.chmod(bak, 0o600)

    # -- message log
    def log(self, chat_id: int, text: str, direction: str, at: datetime, update_id: int | None = None,
            answered: bool = False, voice: bool = False) -> int:
        cur = self.c.execute("INSERT INTO messages (chat_id, update_id, direction, text, at, answered, voice) VALUES (?,?,?,?,?,?,?)",
                             (chat_id, update_id, direction, text, at.isoformat(), int(answered), int(voice)))
        self.c.commit()
        return cur.lastrowid

    def mark_answered(self, msg_id: int) -> None:
        self.c.execute("UPDATE messages SET answered = 1 WHERE id = ?", (msg_id,))
        self.c.commit()

    def bump_attempt(self, msg_id: int) -> None:
        self.c.execute("UPDATE messages SET attempts = attempts + 1 WHERE id = ?", (msg_id,))
        self.c.commit()

    def unanswered(self, since: datetime) -> list[sqlite3.Row]:
        """Inbound messages received since `since` that never got a reply (crash or outage), oldest first."""
        return self.c.execute("SELECT * FROM messages WHERE direction='in' AND answered=0 AND attempts < ? AND at >= ? ORDER BY id",
                              (MAX_ATTEMPTS, since.isoformat())).fetchall()

    def purge(self, now: datetime, days: int = RETENTION_DAYS) -> int:
        cutoff = (now - timedelta(days=days)).isoformat()
        n = self.c.execute("DELETE FROM messages WHERE at < ?", (cutoff,)).rowcount
        self.c.execute("DELETE FROM pending_actions WHERE created_at < ?", (cutoff,))
        self.c.execute("DELETE FROM audit_log WHERE at < ?", ((now - timedelta(days=365)).isoformat(),))
        self.c.execute("DELETE FROM health_events WHERE at < ?", (cutoff[:19],))
        self.c.execute("DELETE FROM retry_queue WHERE queued_at < ?", (cutoff,))
        self.c.commit()
        return n

    def messages(self, chat_id: int) -> list[sqlite3.Row]:
        return self.c.execute("SELECT * FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,)).fetchall()

    def recent_messages(self, chat_id: int, n: int) -> list[sqlite3.Row]:
        """The last `n` log rows of the chat, oldest first."""
        rows = self.c.execute("SELECT * FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?", (chat_id, n)).fetchall()
        return rows[::-1]

    # -- retry queue (ADR 0021)
    def queue_add(self, chat_id: int, text: str, at: datetime) -> int:
        """Keep a message for the model's return. Idempotent: the same text already queued is not stored twice."""
        r = self.c.execute("SELECT id FROM retry_queue WHERE chat_id=? AND text=? AND status='queued'", (chat_id, text)).fetchone()
        if r:
            return r["id"]
        cur = self.c.execute("INSERT INTO retry_queue (chat_id, text, queued_at) VALUES (?,?,?)", (chat_id, text, at.isoformat()))
        self.c.commit()
        return cur.lastrowid

    def queue_list(self, chat_id: int) -> list[sqlite3.Row]:
        """Queued messages, oldest first."""
        return self.c.execute("SELECT * FROM retry_queue WHERE chat_id=? AND status='queued' ORDER BY id", (chat_id,)).fetchall()

    def queue_close(self, qid: int, status: str, at: datetime) -> None:
        self.c.execute("UPDATE retry_queue SET status=?, closed_at=? WHERE id=?", (status, at.isoformat(), qid))
        self.c.commit()

    def queue_clear(self, chat_id: int, at: datetime) -> int:
        n = self.c.execute("UPDATE retry_queue SET status='cleared', closed_at=? WHERE chat_id=? AND status='queued'",
                           (at.isoformat(), chat_id)).rowcount
        self.c.commit()
        return n

    # -- update_id dedupe
    def last_update_id(self) -> int:
        r = self.c.execute("SELECT value FROM meta WHERE key='last_update_id'").fetchone()
        return int(r["value"]) if r else 0

    def set_last_update_id(self, n: int) -> None:
        self.c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_update_id', ?)", (str(n),))
        self.c.commit()

    # -- pending actions
    def add_pending(self, chat_id: int, intent: str, args: dict, now: datetime, ttl: timedelta = PENDING_TTL) -> int:
        self.c.execute("UPDATE pending_actions SET status='superseded' WHERE chat_id=? AND status='open'", (chat_id,))
        cur = self.c.execute("INSERT INTO pending_actions (chat_id, intent, args_json, args_hash, created_at, expires_at) VALUES (?,?,?,?,?,?)",
                             (chat_id, intent, json.dumps(args, sort_keys=True, ensure_ascii=False), args_hash(intent, args),
                              now.isoformat(), (now + ttl).isoformat()))
        self.c.commit()
        return cur.lastrowid

    def get_pending(self, pid: int) -> sqlite3.Row | None:
        return self.c.execute("SELECT * FROM pending_actions WHERE id=?", (pid,)).fetchone()

    def claim_pending(self, pid: int) -> bool:
        """Atomic open -> executing. True for exactly one caller (typed 'evet', a button, a double tap)."""
        n = self.c.execute("UPDATE pending_actions SET status='executing' WHERE id=? AND status='open'", (pid,)).rowcount
        self.c.commit()
        return n == 1

    # -- the message that carries the current inline keyboard: (message_id, pending_id)
    def get_kb(self, chat_id: int) -> tuple[int, int] | None:
        r = self.c.execute("SELECT value FROM meta WHERE key=?", (f"kb:{chat_id}",)).fetchone()
        return tuple(json.loads(r["value"])) if r else None

    def set_kb(self, chat_id: int, message_id: int | None, pid: int | None = None) -> None:
        if message_id is None:
            self.c.execute("DELETE FROM meta WHERE key=?", (f"kb:{chat_id}",))
        else:
            self.c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (f"kb:{chat_id}", json.dumps([message_id, pid])))
        self.c.commit()

    def open_pending(self, chat_id: int) -> sqlite3.Row | None:
        return self.c.execute("SELECT * FROM pending_actions WHERE chat_id=? AND status='open' ORDER BY id DESC LIMIT 1", (chat_id,)).fetchone()

    def close_pending(self, pid: int, status: str) -> None:
        self.c.execute("UPDATE pending_actions SET status=? WHERE id=?", (status, pid))
        self.c.commit()

    @staticmethod
    def pending_state(row, now: datetime) -> str:
        """'ok' | 'expired' | 'tampered' (stored args no longer match their hash: nothing may run)."""
        if args_hash(row["intent"], json.loads(row["args_json"])) != row["args_hash"]:
            return "tampered"
        return "expired" if now > datetime.fromisoformat(row["expires_at"]) else "ok"

    # -- audit (no message text, no names)
    def audit(self, action: str, detail: dict, now: datetime, chat_id: int | None = None) -> None:
        self.c.execute("INSERT INTO audit_log (at, action, detail, chat_id) VALUES (?,?,?,?)",
                       (now.isoformat(), action, json.dumps(detail, sort_keys=True), chat_id))
        self.c.commit()

    def audit_rows(self, action: str | None = None) -> list[sqlite3.Row]:
        q, a = "SELECT * FROM audit_log", ()
        if action:
            q, a = q + " WHERE action = ?", (action,)
        return self.c.execute(q + " ORDER BY id", a).fetchall()

    # -- small key/value state (health counters)
    def get_meta(self, key: str, default: str = "") -> str:
        r = self.c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_meta(self, key: str, value) -> None:
        self.c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value)))
        self.c.commit()

    # -- tasks (to-dos); status open | done | removed
    def add_task(self, chat_id: int, title: str, now: datetime, due: str | None = None) -> int:
        cur = self.c.execute("INSERT INTO tasks (chat_id, title, created_at, due) VALUES (?,?,?,?)", (chat_id, title, now.isoformat(), due))
        self.c.commit()
        self.set_meta("last_item", json.dumps({"title": title, "at": now.isoformat()}))        # 'bunu aksam 10 gibi hatirlat' means this one
        return cur.lastrowid

    def open_tasks(self, chat_id: int) -> list[sqlite3.Row]:
        return self.c.execute("SELECT * FROM tasks WHERE chat_id=? AND status='open' ORDER BY id", (chat_id,)).fetchall()

    def close_task(self, task_id: int, status: str, now: datetime) -> None:
        self.c.execute("UPDATE tasks SET status=?, closed_at=? WHERE id=? AND status='open'", (status, now.isoformat(), task_id))
        self.c.commit()

    def set_task_due(self, task_id: int, due: str | None) -> None:
        self.c.execute("UPDATE tasks SET due=? WHERE id=? AND status='open'", (due, task_id))
        self.c.commit()

    # -- finished calendar events (closed in Sebastian's store, never removed from the calendar)
    def mark_event_done(self, chat_id: int, event_id: str, title: str, now: datetime) -> None:
        self.c.execute("INSERT OR IGNORE INTO event_done (chat_id, event_id, title, closed_at) VALUES (?,?,?,?)", (chat_id, event_id, title, now.isoformat()))
        self.c.commit()

    def done_event_ids(self, chat_id: int) -> set[str]:
        return {r[0] for r in self.c.execute("SELECT event_id FROM event_done WHERE chat_id=?", (chat_id,))}

    # -- named lists (ADR 0016); status open | done | removed
    def add_list_item(self, chat_id: int, key: str, text: str, now: datetime) -> int:
        cur = self.c.execute("INSERT INTO list_items (chat_id, list_key, text, created_at) VALUES (?,?,?,?)",
                             (chat_id, key, text, now.isoformat()))
        self.c.commit()
        return cur.lastrowid

    def list_items(self, chat_id: int, key: str, status: str = "open") -> list[sqlite3.Row]:
        return self.c.execute("SELECT * FROM list_items WHERE chat_id=? AND list_key=? AND status=? ORDER BY id",
                              (chat_id, key, status)).fetchall()

    def get_task(self, task_id: int) -> sqlite3.Row | None:
        return self.c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()

    def get_list_item(self, item_id: int) -> sqlite3.Row | None:
        return self.c.execute("SELECT * FROM list_items WHERE id=?", (item_id,)).fetchone()

    def set_list_item_status(self, item_id: int, status: str, now: datetime) -> None:
        """open <-> done for one list item (the tap toggle); a removed item is never revived."""
        self.c.execute("UPDATE list_items SET status=?, closed_at=? WHERE id=? AND status IN ('open','done')",
                       (status, now.isoformat() if status == "done" else None, item_id))
        self.c.commit()

    def close_list_items(self, ids: list[int], status: str, now: datetime) -> int:
        """open -> done | removed for the given ids (only rows that are still open); returns how many changed."""
        n = 0
        for i in ids:
            n += self.c.execute("UPDATE list_items SET status=?, closed_at=? WHERE id=? AND status='open'",
                                (status, now.isoformat(), i)).rowcount
        self.c.commit()
        return n

    def clear_list(self, chat_id: int, key: str, now: datetime) -> None:
        """Every open and done item of the list becomes removed."""
        self.c.execute("UPDATE list_items SET status='removed', closed_at=? WHERE chat_id=? AND list_key=? AND status IN ('open','done')",
                       (now.isoformat(), chat_id, key))
        self.c.commit()

    # -- NL daily cap
    def nl_calls(self, day: str, chat_id: int) -> int:
        r = self.c.execute("SELECT calls FROM nl_usage WHERE day=? AND chat_id=?", (day, chat_id)).fetchone()
        return r["calls"] if r else 0

    def nl_bump(self, day: str, chat_id: int) -> None:
        self.c.execute("INSERT INTO nl_usage (day, chat_id, calls) VALUES (?,?,1) ON CONFLICT(day, chat_id) DO UPDATE SET calls = calls + 1",
                       (day, chat_id))
        self.c.commit()

    # -- conversation (ask-until-sure state; survives restarts)
    def get_conversation(self, chat_id: int) -> dict | None:
        r = self.c.execute("SELECT state_json FROM conversation WHERE chat_id=?", (chat_id,)).fetchone()
        return json.loads(r["state_json"]) if r else None

    def set_conversation(self, chat_id: int, state: dict) -> None:
        self.c.execute("INSERT OR REPLACE INTO conversation (chat_id, state_json) VALUES (?,?)", (chat_id, json.dumps(state, ensure_ascii=False)))
        self.c.commit()

    def clear_conversation(self, chat_id: int) -> None:
        self.c.execute("DELETE FROM conversation WHERE chat_id=?", (chat_id,))
        self.c.commit()

    # -- agent mode (ADR 0019)
    def agent_session(self, chat_id: int) -> sqlite3.Row | None:
        return self.c.execute("SELECT * FROM agent_sessions WHERE chat_id=?", (chat_id,)).fetchone()

    def agent_start_session(self, chat_id: int, session_id: str, prompt_version: str, now: datetime) -> None:
        self.c.execute("INSERT OR REPLACE INTO agent_sessions (chat_id, session_id, prompt_version, turns, started_at, last_at) "
                       "VALUES (?,?,?,0,?,?)", (chat_id, session_id, prompt_version, now.isoformat(), now.isoformat()))
        self.c.commit()

    def agent_touch_session(self, chat_id: int, now: datetime) -> None:
        self.c.execute("UPDATE agent_sessions SET turns = turns + 1, last_at = ? WHERE chat_id = ?", (now.isoformat(), chat_id))
        self.c.commit()

    def agent_drop_session(self, chat_id: int) -> None:
        self.c.execute("DELETE FROM agent_sessions WHERE chat_id=?", (chat_id,))
        self.c.commit()

    def note_set(self, chat_id: int, key: str, value: str, now: datetime) -> None:
        self.c.execute("INSERT OR REPLACE INTO agent_notes (chat_id, key, value, updated_at) VALUES (?,?,?,?)",
                       (chat_id, key, value, now.isoformat()))
        self.c.commit()

    def note_delete(self, chat_id: int, key: str) -> None:
        self.c.execute("DELETE FROM agent_notes WHERE chat_id=? AND key=?", (chat_id, key))
        self.c.commit()

    def notes(self, chat_id: int) -> list[sqlite3.Row]:
        return self.c.execute("SELECT * FROM agent_notes WHERE chat_id=? ORDER BY key", (chat_id,)).fetchall()

    def grant_mint(self, chat_id: int, pending_id: int, args_hash_: str, now: datetime) -> int:
        """A grant for ONE exact draft version. Minted by code only; the model has no tool for it."""
        self.c.execute("UPDATE agent_grants SET used=1 WHERE chat_id=? AND used=0", (chat_id,))      # one live grant at a time
        cur = self.c.execute("INSERT INTO agent_grants (chat_id, pending_id, args_hash, created_at) VALUES (?,?,?,?)",
                             (chat_id, pending_id, args_hash_, now.isoformat()))
        self.c.commit()
        return cur.lastrowid

    def grant_consume(self, chat_id: int, pending_id: int, args_hash_: str) -> bool:
        """Atomic: True for exactly one caller holding a live grant for this exact version."""
        n = self.c.execute("UPDATE agent_grants SET used=1 WHERE id = (SELECT id FROM agent_grants WHERE chat_id=? AND pending_id=? "
                           "AND args_hash=? AND used=0 ORDER BY id DESC LIMIT 1)", (chat_id, pending_id, args_hash_)).rowcount
        self.c.commit()
        return n == 1

    def grant_revoke_all(self, chat_id: int) -> None:
        self.c.execute("UPDATE agent_grants SET used=1 WHERE chat_id=? AND used=0", (chat_id,))
        self.c.commit()

    def grant_live(self, chat_id: int) -> sqlite3.Row | None:
        return self.c.execute("SELECT * FROM agent_grants WHERE chat_id=? AND used=0 ORDER BY id DESC LIMIT 1", (chat_id,)).fetchone()
