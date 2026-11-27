"""Minimal deterministic fallback parser (used when no language model is available): slash-free simple patterns for
reading the calendar, adding, moving and deleting events, and birthdays. Produces the same intent dicts as
`nl.validate`. Anything it is not sure about becomes `unclear` and the assistant asks."""
from __future__ import annotations
import re
from datetime import date, timedelta
from .textutil import fold
from . import english, fuzzytime
from .timeparse import blank, find_date, find_duration, find_reminder, find_time, strip_spans

FILLER = {"ekle", "ekler", "eklermisin", "koy", "kaydet", "olustur", "hatirlat", "hatirlatir", "misin", "mi", "bana",
          "lutfen", "takvime", "takvimime", "takvimden", "takvimdeki", "benim", "bir", "icin", "saat", "de", "da",
          "sil", "kaldir", "iptal", "et", "ertele", "tasi", "kaydir", "degistir", "var", "yap", "al", "once", "olarak",
          "ayarla", "olacak", "ona", "onu", "bunu", "artik", "hala", "duruyor", "durdu", "mu", "misin", "miydi"}
LEAD_TRAIL = {"ve", "ayrica", "ama", "ile", "hem", "bir", "de", "da", "ki"}   # dropped at the edges of a title only
DAY_FILLER = {"gunu", "gunune", "gunun", "gune", "gununden", "gunde", "gunudur", "gununu"}  # 'friday day' filler, but the birthday title keeps its 'day' word
LIST_WORDS = re.compile(r"\b(ne var|neler var|programim|planim|gundem|takvimim|takvime bak|takvimi goster|ajanda|bos muyum|musait miyim"
                        r"|(?:program|plan)\w*\s+(?:nasil|neler)|ne yapiyoruz|neler yapiyoruz|yapilacak \w+ neler|islerim neler)\b")
ADD_WORDS = re.compile(r"\b(ekle\w*|koy\w*|kaydet\w*|olustur\w*|hatirlat\w*|ayarla\w*|randevu\w*|var)\b")
DEL_WORDS = re.compile(r"\b(sil\w*|kaldir\w*|iptal et\w*)\b")
DEL_WEAK = re.compile(r"\b(cikar\w*|kalmasin|gerek yok|gerekmiyor)\b")   # only when the text does not also ask to add
MOVE_WORDS = re.compile(r"\b(ertele\w*|tasi\w*|kaydir\w*|degistir\w*)\b")
BIRTH = re.compile(r"dogum gun")


def _title(text: str, spans, birthday: bool = False) -> str | None:
    rest = strip_spans(text, spans)
    drop = FILLER if birthday else FILLER | DAY_FILLER
    words = [w for w in re.findall(r"[^\s,;]+", rest) if fold(w).strip("'’.!?") not in drop and not re.fullmatch(r"['’]\w{0,3}", w)]
    while words and fold(words[0]).strip("'’.!?") in LEAD_TRAIL:     # a dangling conjunction is not part of the subject
        words.pop(0)
    while words and fold(words[-1]).strip("'’.!?") in LEAD_TRAIL:
        words.pop()
    t = " ".join(words).strip(" -:,.'’")
    t = re.sub(r"^['’]+|['’]+$", "", t)
    return (t[0].upper() + t[1:])[:80] if t else None


def parse(text: str, today: date) -> dict:
    if (en := english.parse(text, today)) is not None:     # an unmistakably English opening (ADR 0008); Turkish text never matches
        return en
    text = re.sub(r"(?i)\b(yar[i\u0131]n|bug[u\u00fc]n)(?:[i\u0131u\u00fc]n|ki)\b", r"\1", text)       # 'yarinin programi' -> 'yarin programi'
    f = fold(text)
    rm = find_reminder(text)
    rest = blank(text, rm[1]) if rm else text   # 'one hour before' is a reminder, not a clock time
    d = find_date(rest, today)
    fz = fuzzytime.find(rest)                     # a fuzzy time or range wins over the plain clock reader (ADR 0015)
    ct = find_time(rest)
    if fz and fz.start and not fz.end and not fz.duration and ct and not re.search(r"gibi|civari|sularinda|dogru", fold(fz.said)):
        tm = (ct[0], sorted(ct[1] + fz.spans))        # 'oglen 12de': the clock the owner said beats the day word 'oglen'
        fz = None
    else:
        tm = (fz.start, fz.spans) if fz and fz.start else ct
    spans = (d[1] if d else []) + (tm[1] if tm else []) + (rm[1] if rm else [])
    du = find_duration(text, spans)
    if du:
        spans += du[1]
    elif fz and fz.duration:
        spans += fz.spans
    fuzzy = {"end_time": fz.end.strftime("%H:%M") if fz and fz.end else None, "_fuzzy": {"t": fuzzytime.clock(fz), "said": fz.said},
             "_next_day": fz.next_day, "_ampm": True} if fz and fz.start else {}
    dmin = du[0] if du else (fz.duration if fz else None)
    is_birth = bool(BIRTH.search(f))
    if re.search(r"\b(dogum gunleri|yaklasan dogum|dogum gunu (ne zaman|var mi|listesi))", f) and not ADD_WORDS.search(f) and not d:
        return {"intent": "birthdays"}
    if LIST_WORDS.search(f) or re.search(r"\b(bu hafta|haftalik|haftam|hafta sonu|haftasonu)\b", f):
        if re.search(r"\b(bu hafta|haftalik|haftam|onumuzdeki hafta|hafta sonu|haftasonu)\b", f):
            return {"intent": "list_events", "range": "week", "date": None, "end_date": None}
        if d and d[0] == today + timedelta(days=1):
            return {"intent": "list_events", "range": "tomorrow", "date": None, "end_date": None}
        if d and d[0] != today:
            return {"intent": "list_events", "range": "date", "date": d[0].isoformat(), "end_date": None}
        return {"intent": "list_events", "range": "today", "date": None, "end_date": None}
    if re.fullmatch(r"\s*(merhaba|selam|gunaydin|iyi aksamlar|tesekkurler|sagol|tamam)\W*", f):
        return {"intent": "chitchat"}
    if DEL_WORDS.search(f) or (DEL_WEAK.search(f) and not ADD_WORDS.search(f)):
        match = _title(text, spans + [m.span() for m in DEL_WORDS.finditer(f)] + [m.span() for m in DEL_WEAK.finditer(f)])
        if not match:
            return {"intent": "unclear", "question": None}
        return {"intent": "delete_event", "match": match, "date": d[0].isoformat() if d else None, "scope": "this"}
    if MOVE_WORDS.search(f) and (d or tm):
        match = _title(text, spans + [m.span() for m in MOVE_WORDS.finditer(f)])
        ch = {}
        if d:
            ch["date"] = d[0].isoformat()
        if tm:
            ch["time"] = tm[0].strftime("%H:%M")
        if match:
            return {"intent": "edit_event", "match": match, "date": None, "scope": "this", "changes": ch}
        return {"intent": "unclear", "question": None}
    if rm and not d and not tm:  # '<event> remind me 1 hour before' on an existing event
        match = _title(text, spans)
        if match:
            return {"intent": "edit_event", "match": match, "date": None, "scope": "this", "changes": {"reminder_min": rm[0]}}
    if is_birth and (d or ADD_WORDS.search(f)):
        title = _title(text, spans, birthday=True)
        return {"intent": "add_event", "title": title, "date": d[0].isoformat() if d else None, "time": None, "end_time": None,
                "duration_min": None, "all_day": True, "reminder_min": rm[0] if rm else None, "location": None,
                "notes": None, "recurrence": "yearly", "tag": "birthday"}
    if d or tm or ADD_WORDS.search(f):
        allday = bool(re.search(r"\b(tum gun|gun boyu|tam gun)\b", f))
        if allday:
            spans += [m.span() for m in re.finditer(r"\b(tum gun|gun boyu|tam gun)\b", f)]
        rec = "weekly" if re.search(r"\bher hafta\b|\bhaftalik tekrar", f) else "daily" if re.search(r"\bher gun\b", f) else ""
        if rec:
            spans += [m.span() for m in re.finditer(r"\bher (hafta|gun)\b|\bhaftalik tekrar\w*", f)]
        title = _title(text, spans)
        return {"intent": "add_event", "title": title, "date": d[0].isoformat() if d else None,
                "time": tm[0].strftime("%H:%M") if tm else None, "end_time": None,
                "duration_min": dmin, "all_day": allday, "reminder_min": rm[0] if rm else None,
                "location": None, "notes": None, "recurrence": rec, "tag": "", **fuzzy}
    return {"intent": "unclear", "question": None}


# -- simple mode (ADR 0021): when the language model is unavailable only high-confidence, single-event messages are accepted
CLAUSE_WORDS = {"ve", "ayrica", "ama", "fakat", "ancak", "hem", "sonra", "ardindan", "birde", "ek", "olarak"}
READ_INTENTS = ("list_events", "birthdays", "chitchat", "help")
MAX_WORDS, MAX_TITLE_WORDS = 12, 6


def _count(rest: str, finder, limit: int = 4) -> int:
    n = 0
    while n < limit and (hit := finder(rest)):
        n += 1
        rest = blank(rest, hit[1])
    return n


def safe(text: str, today: date) -> dict | None:
    """The intent when the rules read `text` with HIGH confidence and it holds exactly ONE thing (one subject, at most one time
    and one date, no clause conjunctions, no leftover words); None when it is complex and must wait for the model. Pure."""
    f = fold(text)
    words = re.findall(r"[^\s,;]+", f)
    if not words or len(words) > MAX_WORDS or "\n" in text.strip() or ";" in text or text.count(",") > 1:
        return None
    rm = find_reminder(text)
    rest = blank(text, rm[1]) if rm else text
    fz = fuzzytime.find(rest)
    if fz and fz.start:
        rest = blank(rest, fz.spans)
        n_times = 1 + _count(rest, find_time)
    else:
        n_times = _count(rest, find_time)
    if n_times > 1 or _count(rest, lambda t: find_date(t, today)) > 1:
        return None
    if len(re.findall(r"\bvar\b", f)) > 1 or len(re.findall(r"\b(ekle\w*|koy\w*|kaydet\w*|olustur\w*)\b", f)) > 1:
        return None
    p = parse(text, today)
    name = p["intent"]
    if name in READ_INTENTS:
        return p
    if name == "unclear":
        return None
    title = p.get("title") or p.get("match")
    if not title:
        return None
    tw = [fold(w).strip("'’.!?") for w in title.split()]
    if len(tw) > MAX_TITLE_WORDS or any(w in CLAUSE_WORDS for w in tw) or any(re.search(r"\d", w) for w in tw):
        return None
    if name == "add_event" and not (p.get("date") or p.get("time") or p.get("all_day")):
        return None
    return p
