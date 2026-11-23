"""Tentative planning (ADR 0024): 'bu gorevleri de planla' places the open tasks into the free gaps of the calendar and shows ONE
draft plan as an ordinary multi-event preview. Nothing is written without the owner's confirmation. Saving an event titled like a
task closes that task (existing behaviour), so the plan replaces the to-do list entries it covers."""
from __future__ import annotations
import re
from datetime import date, datetime, timedelta

from . import i18n
from .merge import merge
from .models import EventDraft
from .textutil import default_duration, fold, title_match

DAY_START, DAY_END = 9, 21          # planning window (local hours)
GAP = timedelta(minutes=10)         # breathing room between placed items
MAX_DAYS = 3                        # never spill further than today + 2

# 'planla', 'planlar misin', 'planlayalim', 'plana al', 'plan yap', 'yerlestir' (a request to place items, not a statement of one)
ASK = re.compile(r"\b(?:planla(?:r\s+misin|yal[i]m|yabilir\w*|sana|yin|n)?|plana\s+(?:al|koy|yerlestir)\w*|plan\s+(?:yap|olustur|cikar)\w*|yerlestir\w*)\b")
REFS = re.compile(r"\b(?:bu|su|bunlari|sunlari|hepsini|digerlerini|kalanlari|gorev\w*|islerimi)\b")
CLOCK = re.compile(r"\b\d{1,2}\s*(?:[:.]\s*\d{2}|de|da|te|ta)\b|\bsaat\b")


def asks_for_plan(text: str) -> bool:
    low = fold(text)
    return bool(ASK.search(low)) and bool(REFS.search(low)) and not CLOCK.search(low)


def target_day(text: str, today: date) -> date | None:
    low = fold(text)
    if re.search(r"\byarin\w*", low):
        return today + timedelta(days=1)
    return None


def _ceil(t: datetime, minutes: int = 15) -> datetime:
    t = t.replace(second=0, microsecond=0)
    extra = (-(t.minute % minutes)) % minutes
    return t + timedelta(minutes=extra)


def place(titles: list[str], busy: list[tuple[datetime, datetime]], now: datetime, first_day: date, tz) -> tuple[list[EventDraft], list[str]]:
    """Greedy, in the owner's order: each title takes the first free gap long enough, today (rest of the day) then the next days.
    Returns (drafts, titles that did not fit)."""
    drafts: list[EventDraft] = []
    rest = list(titles)
    for off in range(MAX_DAYS):
        day = first_day + timedelta(days=off)
        lo = datetime(day.year, day.month, day.day, DAY_START, tzinfo=tz)
        hi = datetime(day.year, day.month, day.day, DAY_END, tzinfo=tz)
        if day == now.date():
            lo = max(lo, _ceil(now.astimezone(tz) + timedelta(minutes=15)))
        taken = sorted([b for b in busy if b[0] < hi and b[1] > lo] + [(d.start, d.end) for d in drafts if lo <= d.start < hi])
        left = []
        for title in rest:
            dur = timedelta(minutes=default_duration(title))
            cur, spot = lo, None
            for s, e in taken + [(hi, hi)]:
                if s - cur >= dur and cur + dur <= hi:
                    spot = cur
                    break
                cur = max(cur, e + GAP)
            if spot is None:
                left.append(title)
                continue
            d = EventDraft(title, spot, spot + dur)
            drafts.append(d)
            taken = sorted(taken + [(d.start, d.end)])
        rest = left
        if not rest:
            break
    return drafts, rest


class PlanningMixin:
    def _tentative_plan(self, text: str, now: datetime) -> list[str] | None:
        """'bu gorevleri de planla': a draft plan for the open tasks (or only the ones the message names). None when the message is
        not such a request or a preview is already open."""
        if not asks_for_plan(text) or self.store.open_pending(self.owner):
            return None
        rows = self.store.open_tasks(self.owner)
        if not rows:
            return [i18n.t("task_list_empty")]
        low = fold(text)
        named = [r["title"] for r in rows if title_match(low, r["title"]) and len(low.split()) > 3]
        titles = named or [r["title"] for r in rows]
        today = now.date()
        first = target_day(text, today) or today
        if first == today and now.hour >= DAY_END - 1:
            first = today + timedelta(days=1)
        try:
            d0 = datetime(first.year, first.month, first.day, tzinfo=self.tz)
            finished = self.store.done_event_ids(self.owner)
            evs = [e for e in merge(self.hub.events(d0, d0 + timedelta(days=MAX_DAYS))) if not e.all_day and e.event_id not in finished]
        except Exception:  # noqa: BLE001
            return [i18n.t("calendar_down")]
        drafts, left = place(titles, [(e.start, e.end) for e in evs], now, first, self.tz)
        if not drafts:
            return [i18n.t("plan_no_room")]
        from .assistant import draft_to_dict
        note = i18n.t("plan_draft_note") + (("\n" + i18n.t("plan_left_out", x=", ".join(left))) if left else "")
        return self._propose("add_event", {"drafts": [draft_to_dict(d) for d in drafts]}, drafts, now, note=note)
