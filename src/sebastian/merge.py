from __future__ import annotations
from dataclasses import replace
from datetime import timedelta
from .models import Event


def merge(events: list[Event], back_to_back: timedelta = timedelta(minutes=10)) -> list[Event]:
    seen: dict[tuple, Event] = {}
    for e in sorted(events, key=lambda x: (x.start, x.title)):
        key = (e.uid or e.title.lower(), e.start, e.end)
        if key not in seen:
            seen[key] = e
    out = list(seen.values())
    timed = [i for i, e in enumerate(out) if not e.all_day]
    flags: dict[int, set[str]] = {i: set() for i in range(len(out))}
    for a, i in enumerate(timed):
        for j in timed[a + 1:]:
            x, y = out[i], out[j]
            if y.start < x.end:
                flags[i].add("conflict"); flags[j].add("conflict")
            elif y.start - x.end <= back_to_back:
                flags[i].add("back-to-back"); flags[j].add("back-to-back")
    return [replace(e, flags=tuple(sorted(flags[i]))) for i, e in enumerate(out)]
