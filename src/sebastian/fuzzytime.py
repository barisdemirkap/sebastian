"""Fuzzy Turkish times and ranges (ADR 0015): 'ogleden sonra 4-5 gibi', '4 ile 5 arasi', 'dortle bes arasi', 'aksama
dogru', 'kahvaltida', 'bir saat sursun'. Pure and deterministic; works on the folded text (same length as the original,
so spans map back to the owner's words). `find` returns what was understood or None; nothing is invented."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import time

from .textutil import fold
from .timeparse import MONTHS, NUMWORDS, Span

_N = r"(?:on iki|on bir|bir|iki|uc|dort|bes|alti|yedi|sekiz|dokuz|on|\d{1,2})"
_MON = "|".join(MONTHS)
_AFTER = r"(?![\d.:/-])(?!\s*(?:" + _MON + r"))"
_TAIL = r"(?:\s+(?:arasi\w*|gibi|civari|sularinda))?"
_RANGES = (
    re.compile(rf"(?<![\d.:/])(?:saat\s*)?({_N})\s*-\s*({_N}){_AFTER}{_TAIL}"),
    re.compile(rf"(?<![\d.:/])(?:saat\s*)?({_N})\s*(?:ile|yle|yla|le|la)\s+({_N}){_AFTER}{_TAIL}"),
    re.compile(rf"(?<![\d.:/])(?:saat\s*)?({_N})\s+({_N})\s+arasi\w*"),
)
_GIBI = re.compile(rf"(?<![\d.:/])(?:saat\s*)?({_N})\s*['’]?\s*(?:gibi|civari|sularinda)\b")
# (pattern, hour, minute, next day): the order matters, longer phrases first
_WORDS = (
    (re.compile(r"\bogle yemegi\w*"), 12, 30, False),
    (re.compile(r"\baksama dogru\b"), 18, 0, False),
    (re.compile(r"\boglene dogru\b"), 12, 0, False),
    (re.compile(r"\bsabah erken\w*"), 7, 0, False),
    (re.compile(r"\baksam ?ustu\w*"), 17, 0, False),
    (re.compile(r"\bgece yarisi\w*"), 0, 0, True),
    (re.compile(r"\bkahvalt\w*"), 8, 0, False),
    (re.compile(r"\bogle(?:n|yin)?\b(?!\s+(?:sonra|yemegi))"), 12, 0, False),
)
_DUR = (
    re.compile(rf"\b({_N}|yarim|\d{{1,3}})\s*(saat|dakika|dk)\w*\s+surs\w*"),
    re.compile(r"\b(yarim)\s+(saat)\b(?!\s+once)"),
)
_PM, _AM, _NIGHT = re.compile(r"\b(ogleden sonra|aksam\w*|ikindi\w*)"), re.compile(r"\bsabah\w*"), re.compile(r"\bgece\b")


@dataclass(frozen=True)
class Fuzzy:
    start: time | None = None
    end: time | None = None
    duration: int | None = None          # minutes, when the owner said how long
    next_day: bool = False               # 'gece yarisi': midnight at the END of the named day
    spans: list[Span] = field(default_factory=list)
    said: str = ""                       # the owner's own words for the time, for the preview


def _num(tok: str) -> int:
    return int(tok) if tok.isdigit() else int(NUMWORDS[tok])


def _hour(h: int, f: str) -> int:
    """12-hour reading of a bare hour: a day part decides, otherwise 1-6 is the afternoon and 7-11 the morning."""
    if h > 12 or h == 12:
        return h
    if _PM.search(f):
        return h + 12
    if _AM.search(f):
        return h
    if _NIGHT.search(f):
        return h + 12 if h >= 6 else h
    return h + 12 if h <= 6 else h


def _day_part(f: str, start: int) -> int:
    m = None
    for m in re.finditer(r"\b(ogleden sonra|sabah|aksam\w*|ikindi\w*|gece)\s*$", f[:start]):
        pass
    return m.start() if m else start


def find(text: str) -> Fuzzy | None:
    f = fold(text)
    spans: list[Span] = []
    start = end = None
    next_day = False
    for rx in _RANGES:
        m = rx.search(f)
        if not m:
            continue
        a, b = _num(m[1]), _num(m[2])
        if not (1 <= a <= 23 and 1 <= b <= 23 and a != b):
            continue
        ha, hb = _hour(a, f), _hour(b, f)
        if hb <= ha and hb < 12:
            hb += 12
        if hb <= ha or hb > 23:
            continue
        start, end, spans = time(ha, 0), time(hb, 0), [(_day_part(f, m.start()), m.end())]
        break
    if start is None and (m := _GIBI.search(f)) and 1 <= _num(m[1]) <= 23:
        start, spans = time(_hour(_num(m[1]), f), 0), [(_day_part(f, m.start()), m.end())]
    if start is None:
        for rx, h, mi, nd in _WORDS:
            if (m := rx.search(f)):
                start, next_day, spans = time(h, mi), nd, [m.span()]
                break
    duration = None
    for rx in _DUR:
        if (m := rx.search(f)) and not any(s <= m.start() < e for s, e in spans):
            n = 0.5 if m[1] == "yarim" else (int(m[1]) if m[1].isdigit() else _num(m[1]))
            duration = int(n * (60 if m[2] == "saat" else 1))
            spans = spans + [m.span()]
            break
    if start is None and duration is None:
        return None
    said = " ".join(text[s:e] for s, e in sorted(spans) if (e - s) and not (duration and (s, e) == spans[-1])).strip()
    return Fuzzy(start, end, duration, next_day, sorted(spans), said[:60])


def clock(f: Fuzzy) -> str:
    """'16:00-17:00' or '18:00': the interpretation shown to the owner."""
    return f"{f.start:%H:%M}" + (f"-{f.end:%H:%M}" if f.end else "")
