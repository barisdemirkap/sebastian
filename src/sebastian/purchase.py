"""Broad purchase-wording classifier (ADR 0017). Pure: no store, no model, no calendar.

`classify` answers "is this utterance a shopping-list add?" for the wordings the owner actually uses ('X alinacak Y
alinacak', 'X lazim', 'X almamiz lazim', 'X al', 'X bitti/kalmadi/tukendi', 'listeye X', 'market: X, Y', 'X'i unutma',
'eve X lazim'). Items that look like events (appointment words) are not list items; a day, date or clock time next to a
purchase wording makes the utterance ambiguous (`event_like`), which the assistant turns into one numbered choice.
Works on folded text (same length as the original, so the owner's own casing is kept in item names)."""
from __future__ import annotations
import re
from datetime import date

from . import i18n
from .lists import split_items
from .textutil import fold, title_words
from .timeparse import find_date, strip_spans

# verb phrases that end a purchase segment; longer first. 'weak' verbs are read only when nothing else speaks against
_VERBS = r"(?:almamiz lazim|almam lazim|almamiz gerek\w*|almam gerek\w*|alinacaklar|alinacak|alinmali|alalim|alinsin|lazim|gerek\w*|bitti|bitmis|bitiyor|kalmadi|tukendi|tukenmis|al)"
_SEG = re.compile(rf"\s*(?P<items>.+?)\s+(?P<verb>{_VERBS})(?=\s|$)", re.S)
_RANOUT = ("bitti", "bitmis", "bitiyor", "kalmadi", "tukendi", "tukenmis")
_PREFIX = re.compile(r"^(?:(?:listeye|listemize|listeme|alinacaklara|alisveris listesine|market listesine)\s+|(?:market|alisveris|alinacaklar|liste)\s*[:\-]\s*)(?P<items>.+)$")
_UNUTMA = re.compile(r"^(?P<items>.+?)\s+unutma\w*$")
_LEAD_FILLER = re.compile(r"^(?:eve|evde|bize|markete|marketten|ayrica|bir de|de|ve)\s+")
_EXPLICIT = re.compile(r"\b(ekle\w*|koy\w*|kaydet\w*|hatirlat\w*|sil\w*|kaldir\w*|iptal\w*|ertele\w*|tasi\w*)\b")
_ACTION = re.compile(r"\w+(?:m[ae]m|m[ae]k|[ae]cag\w+|[ae]ceg\w+)$")     # 'gitmem', 'yapacagim': a task, not a thing to buy
_CLOCK = re.compile(r"(?<![\d.:])\d{1,2}[.:]\d{2}(?![\d])|\bsaat\s*\d")
_DAYWORD = re.compile(r"\b(bugun|yarin|obur gun|haftaya|hafta sonu|pazartesi|sali|carsamba|persembe|cuma|cumartesi|pazar|sabah|aksam|ogle\w*|gece|ikindi)\w*")
_DROP = re.compile(rf"{_CLOCK.pattern}|\bsaat\s*\d{{1,2}}\w*|{_DAYWORD.pattern}|\bgun\w*")
_FOOD_INFINITIVE = ("ekmek", "mercimek", "kaymak", "yemek", "kepek")      # look like '-mek' verbs but are things to buy
_NONITEM = i18n.APPOINTMENT_STEMS + ("rapor", "bilet", "vize", "ehliyet")


def _ok_segment(f: str) -> bool:
    words = title_words(f)
    return bool(words) and len(words) <= 8 and not any(w.startswith(_NONITEM) for w in words) \
        and not any(_ACTION.fullmatch(w) and not w.startswith("alm") and not w.endswith(_FOOD_INFINITIVE) for w in words)


def _items(original: str, span: tuple[int, int]) -> list[str]:
    raw = original[span[0]:span[1]]
    spans = [m.span() for m in _DROP.finditer(fold(raw))] + (find_date(raw, date(2000, 1, 3)) or (None, []))[1]
    raw = strip_spans(raw, spans)
    raw = raw[len(m[0]):] if (m := _LEAD_FILLER.match(fold(raw))) else raw
    raw = raw.strip()
    if (a := re.search(r"(?<=\w)['\u2019](?:yi|yu|i|u)$", fold(raw))):       # fold turns the dotless i and umlauts into plain letters
        raw = raw[:a.start()]
    return split_items(raw)


def classify(text: str, today: date | None = None) -> dict | None:
    """{'items': [...], 'event_like': bool, 'ranout': bool, 'done_like': bool} or None when the text is not a purchase wording."""
    f = re.sub(r"[.!]+$", "", fold(text).strip()).strip()
    if not f or "?" in f or _EXPLICIT.search(f):
        return None
    spans: list[tuple[int, int]] = []
    ranout = done_like = False
    if (m := _PREFIX.match(f)):
        spans = [m.span("items")]
    elif (m := _UNUTMA.match(f)):
        spans = [m.span("items")]
    else:
        pos = 0
        while pos < len(f) and (m := _SEG.match(f, pos)):
            ranout = ranout or m["verb"] in _RANOUT
            done_like = done_like or m["verb"] in ("bitti", "bitmis")      # 'camasir bitti' may mean a finished chore
            spans.append(m.span("items"))
            pos = m.end()
        if not spans or f[pos:].strip():
            return None                                   # the text must END in a verb: 'ekmek al bence' is nothing
    if not all(_ok_segment(f[a:b]) for a, b in spans):
        return None
    items: list[str] = []
    for span in spans:
        for it in _items(text.strip(), span):
            if it.lower() not in {x.lower() for x in items}:
                items.append(it)
    if not items:
        return None
    event_like = bool(_CLOCK.search(f) or _DAYWORD.search(f) or find_date(text, today or date(2000, 1, 3)))
    return {"items": items, "event_like": event_like, "ranout": ranout, "done_like": done_like}
