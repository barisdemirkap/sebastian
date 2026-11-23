"""Multi-part scheduling utterances about an EXISTING event (ADR 0017): 'doktor randevusu tam 8.50de evden cikmak icin
8.10da hazir olalim' is an edit of the doctor event plus a reminder, never a new 'be ready' event. Pure: no store, no
model, no calendar. Works on folded text (same length as the original).

`parse` reads the clock times and what they mean (appointment time, ready-by time, leave-home cue); `subject_hit` says
whether an utterance is about an event (fuzzy title match with the synonym groups of `i18n.SUBJECT_GROUPS`)."""
from __future__ import annotations
import re

from . import i18n
from .textutil import fold, same_stem

_TIME = re.compile(r"(?<![\d.:])(\d{1,2})[.:](\d{2})(?![\d])")
_READY = re.compile(r"\bhazir\w*|\bhatirlat\w*")
_LEAVE = re.compile(r"\bevden cik\w*|\byola cik\w*|\bcikmak icin\b|\bcikalim\b|\bcikacagiz\b|\bcikilacak\b|\bcikmam\b|\bayrilal\w*")
_RETIME = re.compile(r"\b(oldu|olmus|alindi|kaldi|kaydi|degisti|cekildi|ertelendi|olarak|saatine)\b")
_EXPLICIT = re.compile(r"\b(ekle\w*|koy\w*|kaydet\w*|sil\w*|kaldir\w*|iptal\w*|ertele\w*|tasi\w*)\b")
_NOT_SUBJECT = {"randevu", "randevusu", "icin", "tam", "saat", "bugun", "yarin", "hazir", "olalim", "olmus", "alindi", "kaldi",
                "hatirlat", "evden", "cikmak", "cikalim", "hazirlan", "lazim", "olsun", "gibi", "kadar", "sonra", "once"}


def without_times(text: str) -> str:
    """The text with the clock times blanked ('8.50' must not be read as a date)."""
    return _TIME.sub(lambda m: " " * len(m[0]), fold(text))


def parse(text: str) -> dict | None:
    """{'ready': (h, m) | None, 'appt': (h, m) | None, 'leave': bool, 'named': bool, 'retime': bool} or None.
    `named`: the appointment time directly follows (or precedes) the word 'randevu'. A reading needs a ready-by time
    or a retime cue ('oldu', 'alindi', 'kaldi')."""
    f = fold(text)
    if "?" in f or _EXPLICIT.search(f):
        return None
    times = list(_TIME.finditer(f))
    if not times or len(times) > 2:
        return None
    ready = next((m for m in times if re.match(r"['\u2019]?\w{0,3}[\s,]+(?:hazir|hatirlat)", f[m.end():])), None)
    others = [m for m in times if m is not ready]
    retime = bool(_RETIME.search(f))
    if ready is None and not (retime and len(times) == 1):
        return None                      # nothing says what the time is for: an ordinary add or edit
    if ready is None and len(times) == 2:
        return None
    appt = others[0] if others else None
    named = bool(appt and (re.search(r"randevu\w*\s+(?:tam\s+)?$", f[:appt.start()]) or re.match(r"\w*\s+(?:tam\s+)?randevu", f[appt.end():])))
    hm = lambda m: (int(m[1]), int(m[2])) if m and int(m[1]) < 24 and int(m[2]) < 60 else None     # noqa: E731
    out = {"ready": hm(ready), "appt": hm(appt), "leave": bool(_LEAVE.search(f)), "named": named, "retime": retime}
    return out if (out["ready"] or out["appt"]) and (not ready or out["ready"]) and (not appt or out["appt"]) else None


def _stem(w: str) -> str:
    return w[:6]


def _group(w: str) -> int | None:
    return next((i for i, g in enumerate(i18n.SUBJECT_GROUPS) if any(w.startswith(x) for x in g)), None)


def subject_words(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", fold(s)) if len(w) >= 3 and w not in _NOT_SUBJECT]


def subject_hit(text: str, title: str) -> bool:
    """True when the utterance names the subject of the event: a shared word stem or a shared synonym group
    (doktor ~ hekim), generic words ('randevu', 'icin', 'hazir') never count."""
    tw, ew = subject_words(text), subject_words(title)
    for a in tw:
        ga = _group(a)
        for b in ew:
            if a == b or (len(a) >= 5 and len(b) >= 5 and _stem(a) == _stem(b)) or same_stem(a, b) or (ga is not None and ga == _group(b)):
                return True
    return False
