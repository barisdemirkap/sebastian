"""English inbound understanding (ADR 0008): dates, times, add-event wording, calendar questions, shopping-list wording.

The same shape as the Turkish parsers and the same rules: pure, deterministic, works on the folded text (same length as
the original, so spans map back to the owner's words), and nothing is invented (no match means None and the assistant
asks). It is additive: `timeparse` falls back to these readers only when the Turkish reader found nothing (an explicit
am/pm or "noon" is read here first, because a Turkish reader would take "10:30 pm" as 10:30), and `rules.parse`,
`lists.parse` and the assistant call the intent-level `parse*` functions below, which accept only unmistakably English
openings. Day and month names and the list names come from the English language pack (`languages/en.py`).

Not covered (see docs/LANGUAGES.md): editing, moving or deleting an event in English, English task statements beyond
`add task` / `my tasks`, recurrence beyond daily / weekly / "every <weekday>", dates like 03/05 (day or month first?),
"midnight", and names of events written as a date."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from . import languages
from .textutil import fold

Span = tuple[int, int]
_PACK = languages.get("en")
_WD = {d.lower(): i for i, d in enumerate(_PACK.days)}
_WD.update({"tues": 1, "weds": 2, "thur": 3, "thurs": 3})
_WD_SHORT = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_MONTHS = {m.lower(): i + 1 for i, m in enumerate(_PACK.months)}
_MONTHS.update({"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12})
_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
        "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty five": 45, "half": 0.5}
_N = r"(?:\d{1,3}(?:\.\d)?|forty five|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|half an?|half)"
_MON = "|".join(sorted(_MONTHS, key=len, reverse=True))
_WDX = "|".join(sorted(list(_WD) + [w for w in _WD_SHORT], key=len, reverse=True))
_WDF = "|".join(sorted(_WD, key=len, reverse=True))
_ORD = r"(?:st|nd|rd|th)?"
_PRE = r"(?:(?:on|by|for)\s+)?"


def _n(tok: str) -> float:
    tok = tok.strip()
    if tok in ("half a", "half an"):
        return 0.5
    return float(tok) if re.fullmatch(r"\d+(?:\.\d)?", tok) else float(_NUM[tok])


def _valid(d: int, m: int, y: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _day_month(d: int, m: int, today: date, year: str | None) -> date | None:
    if year:
        return _valid(d, m, int(year))
    x = _valid(d, m, today.year)
    return _valid(d, m, today.year + 1) if x and x < today else x


# -- dates ---------------------------------------------------------------------------------------------------------------
def find_date(text: str, today: date) -> tuple[date, list[Span]] | None:
    f = fold(text)
    spans: list[Span] = []
    result: date | None = None

    def hit(m, d):
        nonlocal result
        if d is None:
            return
        if result is None:
            result = d
        spans.append(m.span())

    for m in re.finditer(r"(?<![\d-])(\d{4})-(\d{2})-(\d{2})(?!\d)", f):
        hit(m, _valid(int(m[3]), int(m[2]), int(m[1])))
    for m in re.finditer(rf"\b{_PRE}(?:the\s+)?(\d{{1,2}}){_ORD}(?:\s+of)?\s+({_MON})\b\.?(?:,?\s+(\d{{4}}))?(?![\d:])", f):
        hit(m, _day_month(int(m[1]), _MONTHS[m[2]], today, m[3]))
    for m in re.finditer(rf"\b{_PRE}({_MON})\b\.?\s+(\d{{1,2}}){_ORD}\b(?![\d:]|\s*(?:am|pm|a\.m|p\.m))(?:,?\s+(\d{{4}}))?", f):
        if not any(s <= m.start() < e for s, e in spans):
            hit(m, _day_month(int(m[2]), _MONTHS[m[1]], today, m[3]))
    for m in re.finditer(r"\bday after tomorrow\b", f):
        hit(m, today + timedelta(days=2))
    for m in re.finditer(r"\b(?:in\s+)?(\d{1,3}|a|one|two|three|four|five|six|seven|eight|nine|ten)\s+(day|days|week|weeks)(?:\s+(?:from now|time))?\b", f):
        if m[0].startswith("in ") or "from now" in m[0]:
            n = int(m[1]) if m[1].isdigit() else int(_NUM[m[1]])
            hit(m, today + timedelta(days=n * (7 if m[2].startswith("week") else 1)))
    for m in re.finditer(r"\btomorrow\b", f):
        if not any(s <= m.start() < e for s, e in spans):
            hit(m, today + timedelta(days=1))
    for m in re.finditer(r"\b(?:today|tonight|this (?:morning|afternoon|evening))\b", f):
        hit(m, today)
    nxt = today - timedelta(days=today.weekday()) + timedelta(days=7)
    for m in re.finditer(rf"\bnext\s+week\b(?:\s*,?\s*(?:on\s+)?({_WDX})\b)?", f):
        wd = _wd(m[1])
        hit(m, nxt + timedelta(days=wd) if wd is not None else today + timedelta(days=7))
    for m in re.finditer(rf"\bnext\s+({_WDX})\b", f):                         # like the Turkish 'haftaya cuma': that day of next week
        if not any(s <= m.start() < e for s, e in spans) and (wd := _wd(m[1])) is not None:
            hit(m, nxt + timedelta(days=wd))
    for m in re.finditer(rf"\bthis\s+({_WDX})\b", f):                         # the next one, today included
        if (wd := _wd(m[1])) is not None:
            hit(m, today + timedelta(days=(wd - today.weekday()) % 7))
    for m in re.finditer(rf"\b(?:on\s+)?({_WDF})\b", f):
        if not any(s <= m.start() < e for s, e in spans):
            hit(m, today + timedelta(days=(_WD[m[1]] - today.weekday()) % 7 or 7))
    for m in re.finditer(rf"\bon\s+({'|'.join(_WD_SHORT)})\b", f):
        if not any(s <= m.start() < e for s, e in spans):
            hit(m, today + timedelta(days=(_WD_SHORT[m[1]] - today.weekday()) % 7 or 7))
    return (result, sorted(spans)) if result else None


def _wd(tok: str | None) -> int | None:
    return None if tok is None else _WD.get(tok, _WD_SHORT.get(tok))


def names_today_weekday(text: str, today: date) -> bool:
    """A bare weekday name that is today's weekday ('friday' on a Friday): this week or next? The assistant asks."""
    f = fold(text)
    if re.search(r"\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:" + _MON + r")\b|\b(?:" + _MON + r")\b\s+\d|\btoday\b|\bthis\s+(?:" + _WDX + r")|\bnext\b|\btomorrow\b", f):
        return False
    return any(_WD[m[1]] == today.weekday() for m in re.finditer(rf"\b({_WDF})\b", f))


def mentions_date(text: str) -> bool:
    f = fold(text)
    return bool(re.search(rf"\b(today|tonight|tomorrow|next week|this (?:morning|afternoon|evening|week)|{_WDF}|{_MON})\b", f))


# -- times ---------------------------------------------------------------------------------------------------------------
_CLOCK = r"(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])"
_PART_PM = re.compile(r"\b(?:afternoon|evening|tonight)\b|\bat night\b|\bin the night\b")
_PART_AM = re.compile(r"\bmorning\b")
_PART_SPAN = re.compile(r"\b(?:in the |this |at )?(?:morning|afternoon|evening|night)\b|\btonight\b")


def _ampm(h: int, mi: int, tag: str) -> time | None:
    if not (1 <= h <= 12 and 0 <= mi <= 59):
        return None
    pm = tag.lower().startswith("p")
    return time((h % 12) + (12 if pm else 0), mi)


def _daypart_hour(h: int, f: str) -> int:
    """A bare hour with 'evening' / 'afternoon' / 'tonight' / 'at night' is the afternoon; 'night' keeps small hours (2 at night)."""
    if 1 <= h < 12 and _PART_PM.search(f) and not (h < 6 and re.search(r"\bnight\b", f) and not re.search(r"\b(?:tonight|evening)\b", f)):
        return h + 12
    return h


def has_clock_marker(text: str) -> bool:
    """am/pm after a number, or 'noon': read here before the Turkish reader (which would take '10:30 pm' as 10:30)."""
    f = fold(text)
    return bool(re.search(_CLOCK, f) or re.search(r"\b(?:noon|midday)\b", f))


def find_time(text: str) -> tuple[time, list[Span]] | None:
    f = fold(text)
    if (m := re.search(rf"(?<![\d:.])(?:(?:at|by|from|@)\s*)?{_CLOCK}", f)):
        if (t := _ampm(int(m[1]), int(m[2] or 0), m[3])):
            return t, [m.span()]
    if (m := re.search(r"\b(?:(?:at|by|around)\s+)?(?:noon|midday)\b", f)):
        return time(12, 0), [m.span()]
    for m in re.finditer(r"(?<![\d:.-])(?:at|by|@)\s*(\d{1,2})(?::(\d{2}))?(?:\s*o'?clock)?(?![\d:.])(?=\s*(?:$|[,.;!?)]|o'?clock|in the|at night|tonight|for\b|to\b|until|till|on\b|with\b|and\b|-|tomorrow|today|next|this|every|please))", f):
        h, mi = int(m[1]), int(m[2] or 0)
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return time(_daypart_hour(h, f), mi), [m.span()]
    for m in re.finditer(r"(?<![\d:.-])(\d{1,2})\s*o'?clock\b", f):
        h = int(m[1])
        if 1 <= h <= 12:
            return time(_daypart_hour(h, f), 0), [m.span()]
    return None


def find_range(text: str) -> tuple[time, time, list[Span]] | None:
    """'from 2pm to 4pm', '2pm-3:30pm', '2-4pm', '14:00 to 15:30' -> (start, end, span)."""
    f = fold(text)
    t = r"(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?"
    m = re.search(rf"(?<![\d:.])(?:from\s+|between\s+)?{t}\s*(?:-|to|until|till|and)\s*{t}(?![\d:a-z])(?!\s*(?:{_MON})\b)", f)
    if not m:
        return None
    h1, m1, t1, h2, m2, t2 = int(m[1]), int(m[2] or 0), m[3], int(m[4]), int(m[5] or 0), m[6]
    if not t1 and not t2 and not (m[2] and m[5]):
        return None                                    # '10-11' alone could be anything
    if (m[2] is None) != (m[5] is None) and not (t1 or t2):
        return None
    if t1 or t2:
        end = _ampm(h2, m2, t2 or t1)
        start = _ampm(h1, m1, t1 or t2)
        if start and end and start >= end and not t1:
            start = _ampm(h1, m1, "am")                # '11-1pm': 11am to 1pm
    else:
        start, end = (time(h1, m1) if h1 < 24 else None), (time(h2, m2) if h2 < 24 else None)
    if not start or not end or end <= start:
        return None
    return start, end, [m.span()]


def day_part_spans(text: str) -> list[Span]:
    """'in the evening', 'tonight', 'this morning' next to a time: dropped from the title."""
    return [m.span() for m in _PART_SPAN.finditer(fold(text))]


def mentions_time(text: str) -> bool:
    f = fold(text)
    return bool(re.search(r"\b(?:noon|midday|o'?clock)\b|\b(?:at|by)\s+\w+", f))


# -- durations and reminders ---------------------------------------------------------------------------------------------
def find_duration(text: str, skip: list[Span] = ()) -> tuple[int, list[Span]] | None:
    f = fold(text)
    for m in re.finditer(rf"\b(?:for\s+(?:about\s+|around\s+)?)?({_N}|an hour and a half)[\s-]*(hours?|hrs?|minutes?|mins?)\b", f):
        if any(s <= m.start() < e for s, e in skip) or re.match(r"\s*(?:before|early|earlier|ahead|prior|from now)", f[m.end():]) \
                or re.search(r"\bin\s+$", f[:m.start()]):
            continue
        n = _n(m[1])
        return int(n * (60 if m[2].startswith("h") else 1)), [m.span()]
    if (m := re.search(r"\ban hour and a half\b", f)):
        return 90, [m.span()]
    if (m := re.search(r"\b(?:for\s+)?half an hour\b(?!\s+(?:before|early))", f)):
        return 30, [m.span()]
    return None


def find_reminder(text: str) -> tuple[int, list[Span]] | None:
    """'30 minutes before', 'an hour before', 'remind me 1 day before' -> minutes before the start."""
    f = fold(text)
    m = re.search(rf"(?:remind(?:er)?(?:\s+me)?\s+)?({_N})[\s-]*(hours?|hrs?|minutes?|mins?|days?)\s+(?:before|early|ahead|prior)\b(?:\s+(?:the event|it|hand))?", f)
    if not m:
        return None
    unit = 60 if m[2].startswith(("h",)) else 1440 if m[2].startswith("d") else 1
    return int(_n(m[1]) * unit), [m.span()]


def resolve_relative(text: str, now: datetime) -> str:
    """'in 2 hours', 'in 45 minutes', 'in half an hour' name a moment, not a day: the span becomes 'today at HH:MM' (or
    'tomorrow at ...') so the usual date and time readers and the preview show the exact time. Other text is unchanged."""
    f = fold(text)
    m = re.search(rf"\bin\s+({_N})\s*(hours?|hrs?|minutes?|mins?)\b|\bin\s+(half an hour|an hour and a half)\b", f)
    if not m:
        return text
    if m[3]:
        minutes = 30 if m[3] == "half an hour" else 90
    else:
        minutes = int(round(_n(m[1]) * (60 if m[2].startswith("h") else 1)))
    if not 1 <= minutes <= 24 * 60:
        return text
    at = now + timedelta(minutes=minutes)
    at = at.replace(second=0, microsecond=0) + timedelta(minutes=(5 - at.minute % 5) % 5)       # round up to 5 minutes
    word = "today" if at.date() == now.date() else "tomorrow"
    return text[:m.start()] + f"{word} at {at:%H:%M}" + text[m.end():]


# -- intents ---------------------------------------------------------------------------------------------------------------
_POLITE = r"(?:(?:please|pls|kindly|hey|ok|okay|so|can you|could you|would you|will you)\s+)*"
_ADD_LEAD = re.compile(rf"^{_POLITE}(?:i(?:'d| would) like (?:you )?to\s+|i want (?:you )?to\s+)?(?:(add|schedule|book|create|put|set up|set|make|plan|arrange|new|diarise)\b|remind me\b)", re.I)
_CHITCHAT = re.compile(r"^(?:hello|hi|good (?:morning|afternoon|evening)|thanks|thank you|thx)\W*$")
_HELP = re.compile(r"^(?:please\s+)?(?:help(?: me)?|commands?|what can you do|what do you do|how does this work|how do i use you)\W*$")
_QUERY_LEAD = re.compile(r"^(?:(?:please|hey|ok|okay|so)\s+)*(?:what(?:'?s| is| are| do i have| have i got| am i doing| do we have| are we doing)?|show(?: me)?|list|tell me|read(?: me)?|give me|do i have|have i got|am i (?:free|busy)|is there|how(?:'s| is) my|my|today'?s|tomorrow'?s|this week'?s|agenda|schedule|plans|calendar|events|anything)\b")
_QUERY_CUE = re.compile(r"\b(?:on|planned|scheduled|happening|going on|schedule|agenda|calendar|plans?|appointments?|events?|meetings?|free|busy|diary|coming up|upcoming)\b")
_HAVE = re.compile(r"^(?:(?:please|hey|ok|okay|so)\s+)*(?:what do i have|what have i got|do i have|have i got|what do we have|am i doing)\b")
_NOT_CALENDAR = re.compile(r"\b(?:lists?|shopping|groceries|grocery|tasks?|to-?dos?|birthdays?)\b")


def daypart_only(text: str) -> str | None:
    """'evening' / 'in the afternoon' / 'pm' as a whole answer -> 'pm'; 'morning' / 'am' -> 'am'; else None."""
    f = re.sub(r"[.!?]+$", "", fold(text).strip())
    if re.fullmatch(r"(?:in the |at )?(?:afternoon|evening|night|pm|p\.m\.)", f):
        return "pm"
    return "am" if re.fullmatch(r"(?:in the )?(?:morning|am|a\.m\.)", f) else None


def is_help(text: str) -> bool:
    return bool(_HELP.match(fold(text).strip(" .!?,;")))


def _query(f: str, today: date) -> dict | None:
    if not _QUERY_LEAD.match(f) or _NOT_CALENDAR.search(f) or _ADD_LEAD.match(f):
        return None
    d = find_date(f, today)
    week = re.search(r"\b(?:this week|the week|week ahead|weekly|this week's|rest of the week)\b", f)
    upcoming = re.search(r"\b(?:upcoming|coming up|next week|next few days|next 7 days|next seven days)\b", f)
    scope = d or week or upcoming
    if not (_QUERY_CUE.search(f) or (_HAVE.match(f) and scope)):
        return None
    if week:
        return {"intent": "list_events", "range": "week", "date": None, "end_date": None}
    if upcoming:
        return {"intent": "list_events", "range": "upcoming", "date": None, "end_date": None}
    if d and d[0] == today + timedelta(days=1):
        return {"intent": "list_events", "range": "tomorrow", "date": None, "end_date": None}
    if d and d[0] != today:
        return {"intent": "list_events", "range": "date", "date": d[0].isoformat(), "end_date": None}
    return {"intent": "list_events", "range": "today", "date": None, "end_date": None}


_EDGE = {"on", "at", "for", "from", "to", "in", "by", "next", "this", "until", "till", "and", "of", "the", "a", "an", "my", "me", "please", "every", "each"}
_CAL_TAIL = re.compile(r"\s*\b(?:to|in|on|into|onto)\s+(?:my\s+|the\s+|our\s+)?(?:calendar|schedule|agenda|diary)\b", re.I)


def _title(text: str, spans: list[Span]) -> str | None:
    from .timeparse import strip_spans
    t = " ".join(strip_spans(text, spans).split())
    t = _CAL_TAIL.sub("", t)
    t = re.sub(r"(?i)^" + _POLITE + r"(?:i(?:'d| would) like (?:you )?to\s+|i want (?:you )?to\s+)?(?:remind me(?:\s+(?:to|about|of))?|(?:add|schedule|book|create|put|set up|set|make|plan|arrange|diarise)\b)\s*(?:in\s+|on\s+)?(?:me\s+)?", "", t)
    t = re.sub(r"(?i)^(?:(?:an?|the|my)\s+)?(?:new\s+)?(?=\w)", "", t)
    t = re.sub(r"(?i)^(?:event|entry|reminder)\b\s*(?:(?:called|named|titled|for|about|:)\s*)?(?=\w)", "", t)
    t = re.sub(r"(?i)\s*\b(?:please|for me|thanks|thank you)\W*$", "", t)
    words = t.split()
    while words and words[0].lower().strip(",.:;-") in _EDGE - {"a", "an", "the", "my"}:
        words.pop(0)
    while words and words[-1].lower().strip(",.:;-") in _EDGE - {"a", "an", "the", "my"}:
        words.pop()
    t = " ".join(words).strip(" -:,.;'\"")
    return (t[0].upper() + t[1:])[:80] if t else None


_RECUR = re.compile(rf"\b(?:every\s+(day|week|year|month)|(daily|weekly|yearly|annually)|every\s+({_WDX}))\b")
_BIRTH = re.compile(r"\bbirthdays?\b")


def parse(text: str, today: date) -> dict | None:
    """The intent for an English add-event wording, calendar question, greeting or birthday question; None when the text is
    not unmistakably English (the Turkish rules then read it)."""
    f = re.sub(r"[.!?]+$", "", fold(text).strip()).strip()
    if not f:
        return None
    if _CHITCHAT.match(f):
        return {"intent": "chitchat"}
    if re.match(r"^(?:what|when|show me|list|tell me)\b.*\b(?:birthdays?)\b", f) and not _ADD_LEAD.match(f):
        return {"intent": "birthdays"}
    if (q := _query(f, today)):
        return q
    if not _ADD_LEAD.match(f):
        return None
    rm = find_reminder(text)
    rest = text
    if rm:
        from .timeparse import blank
        rest = blank(text, rm[1])
    d = find_date(rest, today)
    rg = find_range(rest)
    tm = None if rg else find_time(rest)
    if not rg and not tm:
        from .timeparse import find_time as any_time
        tm = any_time(rest)
    spans = (d[1] if d else []) + (rg[2] if rg else tm[1] if tm else []) + (rm[1] if rm else []) + day_part_spans(rest)
    du = None if rg else find_duration(text, spans)
    if du:
        spans += du[1]
    allday = bool(re.search(r"\ball[- ]day\b", f))
    if allday:
        spans += [m.span() for m in re.finditer(r"\ball[- ]day\b", f)]
    rec = ""
    if (m := _RECUR.search(f)):
        word = m[1] or m[2] or "week"
        rec = "daily" if word in ("day", "daily") else "yearly" if word in ("year", "yearly", "annually") else "weekly" if word != "month" else ""
        spans.append(m.span())
        if m[3] and not d:
            d = (today + timedelta(days=(_wd(m[3]) - today.weekday()) % 7), [])
    birthday = bool(_BIRTH.search(f))
    title = _title(text, spans)
    return {"intent": "add_event", "title": title, "date": d[0].isoformat() if d else None,
            "time": (rg[0] if rg else tm[0]).strftime("%H:%M") if (rg or tm) and not birthday else None,
            "end_time": rg[1].strftime("%H:%M") if rg else None, "duration_min": du[0] if du else None,
            "all_day": allday or birthday, "reminder_min": rm[0] if rm else None, "location": None, "notes": None,
            "recurrence": "yearly" if birthday else rec, "tag": "birthday" if birthday else "",
            **({"_ampm": True} if re.search(_CLOCK, f) or _PART_PM.search(f) or _PART_AM.search(f) or "noon" in f else {})}


# -- lists ---------------------------------------------------------------------------------------------------------------
_LIST_ALIASES = _PACK.list_aliases
_ALIAS_RX = "|".join(re.escape(a) for _, al in sorted(_LIST_ALIASES.items()) for a in sorted(al, key=len, reverse=True))
_REF = rf"(?:(?:my|the|our|a)\s+)?(?:(?:{_ALIAS_RX})|list)"
_ITEMS_SPLIT = re.compile(r"\s*(?:,|;|\n|\s&\s|\sand\s|\splus\s)\s*")
_AND_QTY = re.compile(r"^\d+(?:[.,]\d+)?\s*(?:x|pcs|pieces|packs?|bottles?|cartons?|boxes|bags?|loaf|loaves|dozen|kg|g|l|litres?|liters?|lbs?)?\s+", re.I)
_LADD = re.compile(rf"^(?:(?:please|can you|could you|just)\s+)*(?:add|put|write|stick|pop|throw)\s+(?P<items>.+?)\s+(?:to|on|onto|in|into)\s+{_REF}(?:\s+please)?$")
_LADD2 = re.compile(rf"^(?:(?:please|can you|could you)\s+)*add\s+to\s+{_REF}\s*[:,-]?\s*(?P<items>.+)$")
_LOUT = re.compile(r"^(?:we(?:'?re| are)|i(?:'?m| am)|we have run|we ran|i ran|we(?:'ve| have)? run) out of\s+(?P<items>.+)$")
_LSHOW = re.compile(rf"^(?:(?:please|hey)\s+)*(?:what(?:'?s| is)?\s+(?:on|in)\s+{_REF}|(?:show|read|open|display|see|list|check)(?:\s+me)?\s+{_REF}|{_REF}|what (?:do|should) (?:i|we) (?:need to )?(?:buy|get)|what(?:'s| is) on\s+(?:the\s+)?(?:shopping|grocery) list)(?:\s+please)?$")
_LREMOVE = re.compile(rf"^(?:(?:please|can you)\s+)*(?:remove|delete|take|cross off|cross|strike|scratch)\s+(?P<m>.+?)\s+(?:from|off|out of|on)\s+{_REF}(?:\s+please)?$")
_LCLEAR = re.compile(rf"^(?:(?:please|can you)\s+)*(?:clear|empty|wipe|reset|delete)\s+(?:all\s+of\s+)?(?:(?:my|the|our)\s+)?(?:whole\s+|entire\s+)?(?:{_ALIAS_RX}|list)(?:\s+please)?$")
_LCHECK_ALL = re.compile(r"^(?:i\s+)?(?:bought|got|have bought|picked up)\s+(?:everything|it all|them all|all of it|all of them)$|^everything(?:'s| is| has been)? (?:bought|done|checked off)$")
_LCHECK = re.compile(r"^(?:i\s+|we\s+)?(?:bought|got|have bought|have got|picked up|grabbed|just bought|just got)\s+(?P<m>.+)$|^(?P<m2>.+?)\s+(?:is|are|has been|have been)\s+(?:bought|done|purchased|checked off)$")
_NOT_ITEM = {"it", "that", "this", "them", "there", "home", "back", "up", "out", "going", "nothing", "something", "lost", "tired", "better"}
DEFAULT_LIST = "alinacaklar"


def _cap(t: str) -> str:
    t = t.strip(" .,:;-'’\"")
    return (t[0].upper() + t[1:])[:60] if t else ""


def split_items(raw: str) -> list[str]:
    """'milk and eggs, 2 loaves of bread' -> ['Milk', 'Eggs', '2 loaves of bread'] (capitalised, no duplicates)."""
    from .lists import MAX_ITEMS, item_key
    raw = re.sub(r"^(?:some|a few|a bit of|a|an|the)\s+", "", raw.strip(), flags=re.I)
    out: list[str] = []
    for part in _ITEMS_SPLIT.split(raw):
        part = re.sub(r"^(?:some|a few|a|an|the)\s+", "", part.strip(), flags=re.I)
        item = _cap(part)
        if item and item_key(item) not in {item_key(x) for x in out}:
            out.append(item)
    return out[:MAX_ITEMS]


def _key(ref: str | None) -> str:
    f = fold(ref or "")
    for key, aliases in _LIST_ALIASES.items():
        if any(a in f for a in aliases):
            return key
    return DEFAULT_LIST


def parse_list(text: str, today: date = date(2000, 1, 3)) -> dict | None:
    """The same dicts as `lists.parse` ({'kind': 'add'|'show'|'check'|'check_all'|'remove'|'clear', ...}) for English
    wordings; None otherwise. A dated or timed wish is a calendar matter, as in Turkish."""
    f = re.sub(r"[.!?]+$", "", fold(text).strip()).strip()
    f = f.replace("’", "'")
    if not f:
        return None
    start = text.strip()

    def span(m, name):
        return start[m.start(name):m.end(name)]
    if (m := _LADD.match(f) or _LADD2.match(f)):
        if find_date(text, today) or find_time(text):
            return None
        items = split_items(span(m, "items"))
        return {"kind": "add", "list": _key(f), "items": items} if items else None
    if (m := _LOUT.match(f)) and "list" not in f:
        items = split_items(span(m, "items"))
        return {"kind": "add", "list": DEFAULT_LIST, "items": items} if items and not find_date(text, today) else None
    if _LSHOW.match(f):
        return {"kind": "show", "list": _key(f)}
    if _LCLEAR.match(f):
        return {"kind": "clear", "list": _key(f)}
    if _LCHECK_ALL.match(f):
        return {"kind": "check_all"}
    if (m := _LREMOVE.match(f)):
        match = _cap(span(m, "m"))
        return {"kind": "remove", "list": _key(f), "match": match} if match else None
    if (m := _LCHECK.match(f)) and not find_date(text, today) and not find_time(text):
        g = "m" if m["m"] else "m2"
        words = f[m.start(g):m.end(g)].split()
        if words and words[0] not in _NOT_ITEM and len(words) <= 6:
            match = _cap(span(m, g))
            return {"kind": "check", "match": match} if match else None
    return None
