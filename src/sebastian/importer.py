"""Plan migration helper (`sebastian import-plan <json>`): a list of dated plan items becomes ONE pending bulk
add that goes through the same preview + 'evet' flow as everything else. Nothing is written by this module."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import EventDraft
from .textutil import clean

MAX_ITEMS = 100
DEFAULT_DURATION = 60


@dataclass
class PlanResult:
    drafts: list[EventDraft] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def parse_reminder(v) -> int | None:
    """30, '30m', '1h', '1d' -> minutes; None when absent."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise ValueError("reminder")
    if isinstance(v, int):
        n = v
    else:
        m = re.fullmatch(r"\s*(\d{1,4})\s*([mhd]?)\s*", str(v).lower())
        if not m:
            raise ValueError("reminder")
        n = int(m[1]) * {"": 1, "m": 1, "h": 60, "d": 1440}[m[2]]
    if not 0 <= n <= 20160:
        raise ValueError("reminder")
    return n


def load_plan(path: str | Path, tz: ZoneInfo, today: date) -> PlanResult:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read plan file: {exc}") from exc
    items = raw.get("items") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("plan must be a list of items or {\"items\": [...]}")
    res = PlanResult()
    for i, it in enumerate(items[:MAX_ITEMS], 1):
        try:
            res.drafts.append(_draft(it, tz, today))
        except ValueError as exc:
            res.problems.append(f"item {i}: {exc}")
    if len(items) > MAX_ITEMS:
        res.problems.append(f"only the first {MAX_ITEMS} items are used")
    return res


def _draft(it, tz: ZoneInfo, today: date) -> EventDraft:
    if not isinstance(it, dict):
        raise ValueError("not an object")
    title = clean(it.get("title") or "", 80)
    if not title:
        raise ValueError("missing title")
    try:
        d = date.fromisoformat(str(it.get("date")))
    except ValueError:
        raise ValueError("date must be YYYY-MM-DD") from None
    if d < today:
        raise ValueError(f"date {d.isoformat()} is in the past")
    try:
        rem = parse_reminder(it.get("reminder"))
    except ValueError:
        raise ValueError("reminder must be minutes or like 30m / 1h / 1d") from None
    t = it.get("time")
    notes = clean(it.get("notes") or "", 300)
    if t in (None, ""):
        start = datetime.combine(d, time(0, 0), tz)
        return EventDraft(title, start, start + timedelta(days=1), True, "", notes, rem)
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(t))
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise ValueError("time must be HH:MM")
    start = datetime.combine(d, time(int(m[1]), int(m[2])), tz)
    dur = it.get("duration_min", DEFAULT_DURATION)
    if isinstance(dur, bool) or not isinstance(dur, int) or not 1 <= dur <= 1440:
        raise ValueError("duration_min must be 1-1440")
    return EventDraft(title, start, start + timedelta(minutes=dur), False, "", notes, rem)
