"""Deterministic Turkish date/time parsing (rule-based fallback, and the guard against invented model values).

All functions work on the folded text (diacritics removed, lowercase; same length as the original, so spans map
back to the user's own words). Nothing here invents a time: no match means None, and the assistant asks."""
from __future__ import annotations
import re
from datetime import date, time, timedelta
from .textutil import fold

WEEKDAYS = {"pazartesi": 0, "sali": 1, "carsamba": 2, "persembe": 3, "cuma": 4, "cumartesi": 5, "pazar": 6}
MONTHS = {"ocak": 1, "subat": 2, "mart": 3, "nisan": 4, "mayis": 5, "haziran": 6, "temmuz": 7, "agustos": 8,
          "eylul": 9, "ekim": 10, "kasim": 11, "aralik": 12}
NUMWORDS = {"bir": 1, "iki": 2, "uc": 3, "dort": 4, "bes": 5, "alti": 6, "yedi": 7, "sekiz": 8, "dokuz": 9, "on": 10,
            "on bir": 11, "on iki": 12, "yarim": 0.5}
_NUMRE = r"(?:on iki|on bir|bir|iki|uc|dort|bes|alti|yedi|sekiz|dokuz|on|\d{1,3})"
_MON = "|".join(MONTHS)
_WD = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
Span = tuple[int, int]


def _num(s: str) -> int | float:
    return int(s) if s.isdigit() else NUMWORDS[s]


def _valid(d: int, m: int, y: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _next_year_if_past(d: int, m: int, today: date) -> date | None:
    x = _valid(d, m, today.year)
    if x and x < today:
        x = _valid(d, m, today.year + 1)
    return x


def find_date(text: str, today: date) -> tuple[date, list[Span]] | None:
    f = fold(text)
    spans: list[Span] = []
    result: date | None = None

    def hit(m, d):
        nonlocal result
        if result is None:
            result = d
        spans.append(m.span())

    for m in re.finditer(r"(?<![\d.:/])(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})(?!\d)", f):
        y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        if (d := _valid(int(m[1]), int(m[2]), y)):
            hit(m, d)
    for m in re.finditer(rf"(?<![\d.:/])(\d{{1,2}})\s+({_MON})\w*(?:\s+(\d{{4}}))?", f):
        d = _valid(int(m[1]), MONTHS[m[2]], int(m[3])) if m[3] else _next_year_if_past(int(m[1]), MONTHS[m[2]], today)
        if d:
            hit(m, d)
    for m in re.finditer(r"(?<![\d.:/])(\d{1,2})[./](\d{1,2})(?![\d./:])", f):
        if re.search(r"saat\s*$", f[:m.start()]) or re.match(r"(?:'|)(?:[dt][ae]|[yn]?[ae]|[dt][ae]n)\b|\s+gibi\b", f[m.end():]) and int(m[1]) < 24 and int(m[2]) < 60:
            continue                                # '8.10da', '8.10a', '8.10 gibi' are clock times, not 8 October
        if (d := _next_year_if_past(int(m[1]), int(m[2]), today)):
            hit(m, d)
    for m in re.finditer(r"\b(obur gun|ertesi gun|yarindan sonra)\w*", f):
        hit(m, today + timedelta(days=2))
    for m in re.finditer(r"\b(\d{1,3})\s+gun\s+sonra", f):
        hit(m, today + timedelta(days=int(m[1])))
    for m in re.finditer(r"\b(bugun\w*|bu aksam|bu gece|bu sabah|bu ogle\w*)", f):
        hit(m, today)
    for m in re.finditer(r"\byarin\b(?!\s+sonra)", f):
        hit(m, today + timedelta(days=1))
    for m in re.finditer(rf"\b(haftaya|gelecek hafta\w*)(?:\s+({_WD})\w*)?", f):
        monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
        hit(m, monday + timedelta(days=WEEKDAYS[m[2]]) if m[2] else today + timedelta(days=7))
    for m in re.finditer(rf"\bbu\s+({_WD})\w*", f):               # 'this Thursday': the next one, today included
        hit(m, today + timedelta(days=(WEEKDAYS[m[1]] - today.weekday()) % 7))
    for m in re.finditer(rf"\b({_WD})\w*", f):
        if any(s <= m.start() < e for s, e in spans):
            continue
        wd = WEEKDAYS[m[1]]
        hit(m, today + timedelta(days=(wd - today.weekday()) % 7 or 7))
    return (result, sorted(spans)) if result else None


def names_today_weekday(text: str, today: date) -> bool:
    """True when the text names today's weekday bare ('persembe' on a Thursday, no 'bu', 'haftaya', 'bugun' or explicit
    date): the owner may mean today or next week, so the assistant must ask (ADR 0012). `find_date` itself resolves
    such a name to next week."""
    f = fold(text)
    if re.search(r"\d{1,2}[./]\d{1,2}|\bbugun|\bbu\s+(?:" + _WD + r")|\bhaftaya|\bgelecek\s+hafta|\bonumuzdeki|\bsonraki", f):
        return False
    return any(WEEKDAYS[m[1]] == today.weekday() for m in re.finditer(rf"\b({_WD})\w*", f))


_DAYPART_PM = re.compile(r"\b(aksam|ogleden sonra|ikindi)")
_NIGHT = re.compile(r"\bgece\b")
_DAYPART_WORDS = re.compile(r"\b(ogleden sonra|aksam|sabah|gece|ikindi)\s*$")


def _adjust(h: int, f: str) -> int:
    if h < 12 and _DAYPART_PM.search(f):
        return h + 12
    if 6 <= h < 12 and _NIGHT.search(f):
        return h + 12
    return h


def find_time(text: str) -> tuple[time, list[Span]] | None:
    f = fold(text)
    cands: list[tuple[int, int, int, Span]] = []  # (start, hour, minute, span)

    def add(m, h, mi=0):
        if 0 <= h <= 23 and 0 <= mi <= 59:
            cands.append((m.start(), _adjust(h, f), mi, m.span()))

    for m in re.finditer(r"(?<![\d./:])(\d{1,2})[:.](\d{2})(?![\d:])", f):
        asdate = int(m[2]) <= 12 and 1 <= int(m[1]) <= 31 and "." in m[0]
        if asdate and not re.search(r"saat\s*$", f[:m.start()]) and not re.search(r"^\s*(te|ta|de|da)\b", f[m.end():]):
            continue  # looks like dd.mm: the date parser owns it
        add(m, int(m[1]), int(m[2]))
    for m in re.finditer(rf"\bsaat\s*({_NUMRE})\b(?:\s*(bucuk))?(?![\d.:])", f):
        n = _num(m[1])
        add(m, int(n), 30 if m[2] else 0)
    for m in re.finditer(r"\b(on iki|on bir|bir|iki|uc|dort|bes|alti|yedi|sekiz|dokuz|on)\s+bucuk\b", f):   # 'yedi bucuk'
        add(m, int(_num(m[1])), 30)
    for m in re.finditer(r"(?<![\d.:/])(\d{1,2})\s*['’]?\s*(?:te|ta|de|da)\b", f):
        add(m, int(m[1]))
    for m in re.finditer(r"\b(on iki|on bir|iki|uc|dort|bes|alti|yedi|sekiz|dokuz|on|bir)\s*['’]?(?:te|ta|de|da)\b", f):
        add(m, int(_num(m[1])))
    for m in re.finditer(r"(?<![\d.:/])(\d{1,2})['’]?(?:e|a|ye|ya)\b", f):     # dative '4e', '4'e' ('ogleden sonra 4e')
        add(m, int(m[1]))
    cands = [c for c in cands if not any(c is not d and d[3][0] <= c[3][0] and c[3][1] <= d[3][1] for d in cands)]
    if not cands:
        m = re.search(r"\bogle(n|yin|de)\b", f)
        return (time(12, 0), [m.span()]) if m else None
    cands.sort()
    s, h, mi, span = cands[0]
    spans = []
    for c in cands:
        if c[1:3] == (h, mi):
            b = re.search(r"\bsaat\s*$", f[:c[3][0]])
            start, end = (b.start() if b else c[3][0]), c[3][1]
            if (dp := _DAYPART_WORDS.search(f[:start])):          # 'ogleden sonra 4e': the day part belongs to the time
                start = dp.start()
            spans.append((start, end))
    return time(h, mi), spans


def find_reminder(text: str) -> tuple[int, list[Span]] | None:
    """'1 saat once hatirlat', '30 dk once', 'bir gun once' (folded) -> minutes before."""
    f = fold(text)
    m = re.search(rf"({_NUMRE}|yarim)\s*(saat|dakika|dk|gun)\w*\s+once\w*(?:\s+hatirlat\w*)?", f)
    if not m:
        return None
    n = _num(m[1])
    unit = {"saat": 60, "dakika": 1, "dk": 1, "gun": 1440}[m[2]]
    return int(n * unit), [m.span()]


def find_duration(text: str, skip: list[Span] = ()) -> tuple[int, list[Span]] | None:
    f = fold(text)
    for m in re.finditer(rf"({_NUMRE}|yarim)\s*(saat|dakika|dk)\w*", f):
        if any(s <= m.start() < e for s, e in skip) or re.match(r"\s+once", f[m.end():]):
            continue
        n = _num(m[1])
        return int(n * (60 if m[2] == "saat" else 1)), [m.span()]
    return None


def mentions_time(text: str) -> bool:
    """Could the text contain a clock time? Used to reject a time the model made up out of nothing."""
    f = fold(text)
    return bool(re.search(r"\d", f) or re.search(r"\b(on iki|on bir|iki|uc|dort|bes|alti|yedi|sekiz|dokuz|on|bir)\s*['’]?(te|ta|de|da)\b", f)
                or re.search(r"\bsaat\s+\w+", f) or re.search(r"\bogle(n|yin|de)\b", f))


def mentions_date(text: str) -> bool:
    f = fold(text)
    return bool(re.search(r"\d", f) or re.search(rf"\b(bugun|yarin|obur gun|ertesi gun|haftaya|gelecek hafta|bu aksam|bu gece|{_WD}|{_MON})", f))


def strip_spans(text: str, spans: list[Span]) -> str:
    keep = [True] * len(text)
    for s, e in spans:
        for i in range(s, min(e, len(text))):
            keep[i] = False
    return "".join(c for c, k in zip(text, keep) if k)


def blank(text: str, spans: list[Span]) -> str:
    """Replace spans by spaces (indexes stay valid) so later parsers do not re-read them."""
    chars = list(text)
    for s, e in spans:
        for i in range(s, min(e, len(chars))):
            chars[i] = " "
    return "".join(chars)
