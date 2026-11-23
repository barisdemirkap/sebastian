#!/usr/bin/env python3
"""Plain-English conversation health summary for the operator (ADR 0009).

    python scripts/health_report.py [--hours N] [--db PATH]

Reads only the text-free `health_events` table (read-only connection): per chat the signal counts, never messages."""
from __future__ import annotations
import argparse
import os
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta

MEANING = {
    "S1": "the bot was about to repeat itself (reworded)",
    "S2": "three or more unclear replies in a row",
    "S3": "a pending preview expired or could not be verified",
    "S4": "the owner sounded frustrated",
    "S5": "the language-model chain fell back or broke the schema",
    "S6": "an update took longer than 8 seconds",
    "REPORT": "the owner flagged a problem with /bildir",
}


def summarize(conn, since: datetime) -> str:
    rows = conn.execute("SELECT chat_id, signal, count(*) FROM health_events WHERE at>=? GROUP BY chat_id, signal "
                        "ORDER BY chat_id", (since.isoformat(timespec="seconds"),)).fetchall()
    if not rows:
        return "No conversation problems recorded in this period."
    per: dict[int, dict[str, int]] = {}
    for chat, sig, n in rows:
        per.setdefault(chat, {})[sig] = n
    lines = []
    for chat, c in per.items():
        lines.append(f"chat {chat}: " + ", ".join(f"{s}={n}" for s, n in sorted(c.items())))
        for s in sorted(c):
            lines.append(f"    {s}: {MEANING.get(s, 'unknown signal')}")
        if c.get("S4") or c.get("S2") or c.get("REPORT"):
            lines.append("    -> the owner was frustrated or stuck; worth a look")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--db", default=os.environ.get("SEBASTIAN_DB", str(Path.home() / ".config" / "sebastian" / "sebastian.db")))
    a = ap.parse_args(argv)
    if not os.path.exists(a.db):
        print(f"database not found: {a.db}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    try:
        print(f"Conversation health, last {a.hours} h (no message text is stored)")
        print(summarize(conn, datetime.now() - timedelta(hours=a.hours)))
    except sqlite3.Error as exc:
        print(f"cannot read health events: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
