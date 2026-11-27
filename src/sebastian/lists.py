"""Named lists (ADR 0016): deterministic wording parser plus item helpers. Pure: no store, no model, no calendar.

`parse` recognises the explicit phrasings only (add, show, check off, remove, clear); anything else returns None and goes
through the normal flow. Works on folded text (same length as the original, so the owner's own casing is kept in item
names). Which lists exist, their names and aliases live in `i18n.LISTS`."""
from __future__ import annotations
import re
from datetime import date

from . import i18n
from .textutil import fold
from .timeparse import find_date, find_time

MAX_ITEMS, MAX_LEN = 20, 60
_ALIASES = sorted(((a, k) for k, v in i18n.LISTS.items() for a in v["aliases"]), key=lambda x: -len(x[0]))
_NAME = "|".join(re.escape(a) for a, _ in _ALIASES)
_BARE = "|".join(re.escape(a) for a, _ in _ALIASES if " " in a or a in i18n.LIST_SAFE_BARE)
_DAT = r"(?:ne|na|ya|ye|e|a)"
ADD_REF = rf"(?P<ref>(?:{_NAME})(?:\s+listes[a-z]*)?{_DAT}\b|liste(?:me|ye|mize)\b)"
ANY_REF = rf"(?P<ref>(?:{_NAME})[a-z]*\s+liste[a-z]*|(?:{_BARE})[a-z]*|liste[a-z]*)"      # never a bare 'market'/'alisveris' ('marketi sil')
ADD = re.compile(rf"^{ADD_REF}\s+(?P<items>.+?)\s+(?:ekle|ekler misin|ekleyin|ekleyelim|koy|yaz|kaydet)\w*(?:\s+(?:lutfen|bana))*$")
ADD_LAST = re.compile(rf"^(?P<items>.+?)\s+{ADD_REF}\s+(?:ekle|ekler misin|ekleyin|ekleyelim|koy|yaz|kaydet)\w*(?:\s+(?:lutfen|bana))*$")
SHOW = re.compile(rf"^(?:(?:{_BARE})[a-z]*(?:\s+liste[a-z]*)?|liste[a-z]*|(?:{_NAME})[a-z]*\s+liste[a-z]*)"
                  r"(?:\s+(?:ne var|neler var|goster\w*|nedir|neler|oku\w*|bana goster\w*))?$")
CHECK_ALL = re.compile(r"^(?:hepsini|tumunu|hepsi|tumu)\s+(?:aldim|aldik|alindi)$")
CHECK = re.compile(r"^(?P<m>.+?)\s+(?:aldim|aldik|alindi|alinmis)$")
REMOVE = re.compile(rf"^(?P<m>.+?)\s+{ANY_REF}\s+(?:cikar|sil|kaldir)\w*$")
REMOVE_FIRST = re.compile(rf"^{ANY_REF}\s+(?P<m>.+?)\s+(?:cikar|sil|kaldir)\w*$")
CLEAR = re.compile(rf"^(?:(?:tum|butun)\s+)?{ANY_REF}\s+(?:temizle|sifirla|bosalt|sil)\w*$")
QTY = re.compile(r"^\d+(?:[.,]\d+)?\s*(?:adet|litre|lt|l|kg|kilo|gr|g|paket|kutu|tane|sise|sisesi|demet|sepet)?\s+")
SPLIT = re.compile(r"\s*(?:,|;|\n|\s&\s|\sve\s)\s*")


def key_of(ref: str | None) -> str:
    """The list a reference names ('alisveris listesine' -> 'alinacaklar'); a bare 'liste' word is the default list."""
    f = fold(ref or "")
    for alias, key in _ALIASES:
        if f.startswith(alias):
            return key
    return i18n.DEFAULT_LIST


def _cap(t: str) -> str:
    t = t.strip(" .,:;-'’")
    return ((("\u0130" if t[0] == "i" else t[0].upper()) + t[1:]) if t else "")[:MAX_LEN]


def split_items(raw: str) -> list[str]:
    """'3lu priz ve kahve, 2 litre sut' -> ['3lu priz', 'Kahve', '2 litre sut'] (capitalised, quantities kept, no duplicates)."""
    out: list[str] = []
    for part in SPLIT.split(raw.strip()):
        item = _cap(part)
        if item and item_key(item) not in {item_key(x) for x in out}:
            out.append(item)
    return out[:MAX_ITEMS]


def item_key(item: str) -> str:
    """The name without its quantity, folded: '2 litre Sut' and 'sut' are the same item."""
    f = fold(item).strip()
    return QTY.sub("", f).strip() or f


OPEN = re.compile(rf"^{ANY_REF}\s+(?:guncelle|duzenle|degistir|ac|acalim|uzerinde calis)\w*$")


def _typo_fix(f: str) -> str:
    """'alincaklar' -> 'alinacaklar': a word one or two letters off a list alias word is that word (voice and typing slips)."""
    import difflib
    words = {w for a, _ in _ALIASES for w in a.split() if len(w) >= 7}
    def fix(m):
        w = m.group(0)
        if w in words or len(w) < 7:
            return w
        near = difflib.get_close_matches(w, list(words), n=1, cutoff=0.88)
        return near[0] if near else w
    return re.sub(r"[a-z]+", fix, f)


def parse(text: str) -> dict | None:
    """{'kind': 'add', 'list', 'items'} | {'kind': 'show', 'list'} | {'kind': 'check', 'match'} | {'kind': 'check_all'} |
    {'kind': 'remove', 'list', 'match'} | {'kind': 'clear', 'list'} | None."""
    f = re.sub(r"[.!?]+$", "", fold(text).strip()).strip()
    if not f:
        return None
    if (o := OPEN.match(_typo_fix(f))):                  # typo-tolerant, for the open-list wording only
        return {"kind": "open", "list": key_of(o["ref"])}
    if (m := ADD.match(f) or ADD_LAST.match(f)):
        if find_date(text, date(2000, 1, 3)) or find_time(text):
            return None                                  # a dated or timed wish is a calendar matter
        items = split_items(text.strip()[m.start("items"):m.end("items")])
        return {"kind": "add", "list": key_of(m["ref"]), "items": items} if items else None
    if SHOW.match(f):
        return {"kind": "show", "list": key_of(f)}
    if CLEAR.match(f):
        return {"kind": "clear", "list": key_of(CLEAR.match(f)["ref"])}
    if CHECK_ALL.match(f):
        return {"kind": "check_all"}
    if (m := REMOVE.match(f) or REMOVE_FIRST.match(f)):
        match = _cap(text.strip()[m.start("m"):m.end("m")])
        return {"kind": "remove", "list": key_of(m["ref"]), "match": match} if match else None
    if (m := CHECK.match(f)) and not find_date(text, date(2000, 1, 3)) and not find_time(text):
        match = _cap(text.strip()[m.start("m"):m.end("m")])
        return {"kind": "check", "match": match} if match else None
    from . import english
    return english.parse_list(text)                      # English wordings, only when nothing Turkish matched (ADR 0008)


def numbers(match: str) -> list[int] | None:
    """'1 ve 3' / '2.' / '1,3' -> [1, 3]; None when the text is not only numbers."""
    parts = [p for p in re.split(r"[\s,.]+|\bve\b", fold(match)) if p]
    return [int(p) for p in parts] if parts and all(p.isdigit() for p in parts) else None


def digest_rows(store, chat_id: int) -> list[tuple[str, list[str]]]:
    """(display title, open item texts) of every list of the owner, for the scheduler's morning digest."""
    return [(i18n.list_title(k), [r["text"] for r in store.list_items(chat_id, k)]) for k in i18n.LISTS]
