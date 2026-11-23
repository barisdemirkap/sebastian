"""Owner to-dos (ADR 0013): deterministic wording parser. Pure: no store, no model, no calendar.

A task is a line in Sebastian's own list, not a calendar event. `parse` recognises the explicit phrasings only; anything
else returns None and goes through the normal flow. Works on folded text (same length as the original, so the
owner's own casing is kept in titles)."""
from __future__ import annotations
import re
from datetime import date

from . import i18n
from .textutil import fold
from .timeparse import find_date, find_time

LIST = re.compile(r"\b(gorevlerim|gorev listem|gorevleri goster|gorevler|yapilacaklar\w*|yapilacak listem)\b")
ADD = re.compile(r"^(?:gorev|yapilacak)\s+ekle\w*\s*[:,-]?\s*(.+)$|^(?:gorev|yapilacak)\s*[:,-]\s*(.+)$")
NEED = re.compile(r"^(.+?)\s+([a-z]+)m[ae]m\s+(?:lazim|gerek\w*)$")          # 'camasir yapmam lazim', 'X gitmem lazim'
DONE = re.compile(r"^(.+?)\s+(bitti|yapildi|bitirdim|bitirdik|yaptim|tamamlandi|tamamladim|halloldu|hallettim|tamam)$")
FUTURE = re.compile(r"^(.+?)\s+[a-z]*(?:acak|ecek|acagim|ecegim|acagiz|ecegiz)$")      # 'cop aksam cikacak'
DONE_VERB = re.compile(r"(?<=\s)(?:yapildi|bitti|tamamlandi|halloldu|hallettim|yaptim|bitirdim|tamamladim|oldu)(?=\s)")
DEL = re.compile(r"\b(kaldir\w*|sil\w*|cikar\w*|iptal\w*)\b")
MORE_ADD = re.compile(r"^(?:bir\s+(?:tane|sey|de)\s+daha|bir\s+de|bir\s+tane|baska\s+bir\s+(?:sey|tane))\s+(?:ekle\w*|ilave\s+et\w*)\s*[:,-]?\s*(.+)$")
TODO_VERB = re.compile(r"\b\w*(?:yapilacak|yapacagiz|yapacagim|odeyecegiz|odeyecegim|odenecek|odemem|odememiz|alacagiz|alacagim|gidecegiz|gidecegim"
                       r"|[a-z]+(?:acak|ecek|acagiz|ecegiz|acagim|ecegim))\b")
OTHERS = re.compile(r"\bdigerler\w*|\bgerisi\w*|\bkalanlar\w*|\bbaska\w* (?:hepsi|tumu)")
CLOSE_WORD = re.compile(r"\b(done|bitti|yapildi|tamamla\w*|kapat\w*|halloldu|yapbilirsin|yapabilirsin|yap)\b")
KEEP_END = re.compile(r"\b(?:hala|halen|duruyor|dursun|kalsin|kalacak|acik|devam)\b")
AFTER = re.compile(r"(\d{1,2})(?::(\d\d))?\s*['’]?\s*(?:dan|den|tan|ten)\s+sonra")
NAG = re.compile(r"\bsaat\s*basi\w*")
TOMORROW_LIST = re.compile(r"^yarin\w*['’]?\w*\s+(?:liste\w*|gorev\w*|yapilacak\w*)\s+(.+?)\s+ekle\w*$")
FILLER = {"ama", "artik", "onu", "bunu", "ben", "gorevi", "gorevini", "gorev", "bu", "su", "evet", "peki", "de", "da", "ya", "ok", "okey", "tm", "tamamdir"}


def _cap(t: str) -> str:
    t = t.strip(" .,:;-'’")
    return ((("\u0130" if t[0] == "i" else t[0].upper()) + t[1:]) if t else "")[:80]


def _words(original: str, f: str, drop: set[str]) -> str:
    keep = [w for w, fw in zip(re.findall(r"\S+", original), re.findall(r"\S+", f)) if fw.strip("'’.,!?:;") not in drop]
    return " ".join(keep)


def parse_tomorrow(text: str) -> dict | None:
    """"yarin'in listesine dus ve kisisel bakim ekle" -> {'kind': 'add', 'title', 'due': 'tomorrow'}: a task for tomorrow, no time asked."""
    f = re.sub(r"[.!]+$", "", fold(text).strip()).strip()
    if "?" in f or not (m := TOMORROW_LIST.match(f)):
        return None
    title = _cap(text.strip()[m.start(1):m.end(1)])
    return {"kind": "add", "title": title, "due": "tomorrow"} if title else None


def parse(text: str) -> dict | None:
    """{'kind': 'list'} | {'kind': 'add', 'title'} | {'kind': 'done', 'title', 'strong'} | {'kind': 'remove', 'title'} | None."""
    f = fold(text).strip()
    f = re.sub(r"[.!]+$", "", f).strip()
    if not f or "?" in f:
        return None
    n = len(f.split())
    if LIST.search(f) and n <= 5 and not DEL.search(f):
        return {"kind": "list"}
    if (m := ADD.match(f)):
        i = 1 if m[1] else 2
        title = _cap(text.strip()[m.start(i):m.end(i)])
        return {"kind": "add", "title": title} if title else None
    if re.search(r"\bgorev\w*", f) and (d := DEL.search(f)):
        title = _cap(_words(text, f, FILLER | {w for w in f.split() if DEL.fullmatch(w.strip("'’.,!?:;"))}))
        return {"kind": "remove", "title": title} if title else None
    if find_date(text, date(2000, 1, 3)) or find_time(text):
        return None                      # a dated or timed wish is a calendar matter
    if (m := MORE_ADD.match(f)) and TODO_VERB.search(m[1]):
        title = _cap(text.strip()[m.start(1):m.end(1)])           # 'bir tane daha ekle amcamin trafik cezasini odeyecegiz'
        return {"kind": "add", "title": title} if title else None
    if (m := NEED.match(f)):
        stem = f"{m[1]} {m[2]}"
        return {"kind": "add", "title": _cap(text.strip()[:len(stem)].strip())} if m[1].strip() else None
    if (m := DONE.match(f)):
        title = _cap(_words(text[:m.end(1)], f[:m.end(1)], FILLER | {"tamam"}))
        return {"kind": "done", "title": title, "strong": m[2] != "tamam"} if title else None
    if (m := FUTURE.match(f)) and any(w.startswith(i18n.CHORE_STEMS + i18n.TODO_STEMS) for w in m[1].split()):
        return {"kind": "add", "title": _cap(text.strip()[:m.end()]), "implicit": True}     # a chore stated as a plan, no date
    return None


FUT_WORD = re.compile(r"[a-z]*(?:acak|ecek|acagim|ecegim|acagiz|ecegiz)")
TIMEISH = re.compile(r"(?:aksam|sabah|ogle\w*|gece|bugun|yarin|saat|sonra|once|\d{1,2}(?:[.:]\d{2})?\w*)")
REMAINING = re.compile(r"\b(?:neler|ne|hangileri|hangi\s+\w+)\s+kaldi\b|\bkalan\s+(?:isler\w*|gorev\w*|plan\w*|yapilacak\w*)\b|\bkalanlar\b")
META = re.compile(r"\b(?:digerler\w*|gerisi\w*|geri\s+kalan\w*|hepsini|bunlari|yapabilirsin|yapbilirsin|yapabilirsiniz|done|duruyor|dursun"
                  r"|gecmis\s+olan\w*|hallet|sen\s+\w+\s*(?:yap|et))\b")


def is_remaining(text: str) -> bool:
    """'bugun yapilmasi gereken neler kaldi?' -> True: a read of what is left of today (not a list of the whole day)."""
    f = fold(text)
    return bool(REMAINING.search(f)) and not DEL.search(f) and not DONE_VERB.search(" " + f + " ")


def is_meta(text: str) -> bool:
    """An instruction or comment about the list ('X hala duruyor digerlerini done yapabilirsin ...'), not an item to store as a task."""
    f = fold(text)
    return bool(META.search(f)) or len(f.split()) > 9


def has_marker(part: str) -> bool:
    """The clause states something done or still to do ('yaptim', 'cikacak', 'odenecek', 'yapilacak')."""
    f = fold(part)
    return bool(DONE_VERB.search(" " + f + " ") or TODO_VERB.search(f) or any(FUT_WORD.fullmatch(w) for w in f.split()))


def split_clauses(text: str) -> list[str]:
    """Pieces of a multi-clause message: split at , ; . newlines, ' ve ', right after a 'done' verb that is followed by more words
    and right after a 'will be done' verb (with the time words that trail it) when more words follow ('aile hekimi yapildi cop
    aksam cikacak' -> two clauses; 'cop cikacak 18.30da X odenecek aksam 23te' -> two). Same-length folding keeps the owner's casing."""
    f = fold(text)
    cuts = [(m.start(), m.end()) for m in re.finditer(r"[,;\n]+|(?<!\d)\.(?!\d)|\s+ve\s+", f)] + [(m.end(), m.end()) for m in DONE_VERB.finditer(f)]
    toks = list(re.finditer(r"\S+", f))
    for i, m in enumerate(toks):
        if FUT_WORD.fullmatch(m[0].strip("'’.,!?:;")):
            j = i + 1
            while j < len(toks) and TIMEISH.fullmatch(toks[j][0].strip("'’.,!?:;")):
                j += 1
            if j < len(toks):
                cuts.append((toks[j - 1].end(), toks[j - 1].end()))
    parts, prev = [], 0
    for a, b in sorted(cuts):
        if a < prev:
            continue
        parts.append(text[prev:a])
        prev = max(prev, b)
    parts.append(text[prev:])
    return [q for p in parts if len(q := p.strip(" ,;.\n")) > 1]


def _hour(f: str, h: int) -> int:
    return h + 12 if h < 12 and (re.search(r"\baksam\b", f) or h <= 6) else h


def after_time(f: str) -> tuple[int, int] | None:
    """'aksam 6dan sonra' -> (18, 0); None when the text has no 'N-dan sonra'."""
    m = AFTER.search(f)
    return (_hour(f, int(m[1])), int(m[2] or 0)) if m and int(m[1]) < 24 else None


def parse_nag(text: str) -> dict | None:
    """'saat 6dan sonra saat basi hepsini hatirlat' -> {'kind': 'nag', 'at': (18, 0)}: an hourly reminder of every open task."""
    f = fold(text)
    if "?" in f or not NAG.search(f) or not re.search(r"\bhatirlat\w*|\bsoyle\w*|\buyar\w*", f):
        return None
    return {"kind": "nag", "at": after_time(f) or (18, 0)}


def _kept(words: list[str], title: str) -> bool:
    toks = [t for t in re.findall(r"\w+", fold(title)) if len(t) >= 3]
    return any(w.startswith(t) or t.startswith(w) for w in words if len(w) >= 3 for t in toks)


def parse_bulk_done(text: str, titles: list[str]) -> dict | None:
    """'cop acme hala duruyor digerlerini done yapabilirsin' -> {'kind': 'bulk_done', 'keep': [indexes of titles]}: the tasks named
    before 'the others' stay open, every other open task is closed."""
    f = fold(text)
    m = OTHERS.search(f)
    if "?" in f or not m or not CLOSE_WORD.search(f[m.end():]):
        return None
    words = [w for w in re.findall(r"\w+", f[:m.start()]) if w not in FILLER and not KEEP_END.fullmatch(w)]
    return {"kind": "bulk_done", "keep": [i for i, t in enumerate(titles) if _kept(words, t)]}


def parse_after(text: str, titles: list[str]) -> dict | None:
    """'cop ve acme aksam 6dan sonra hue de aksam 6dan sonra' -> {'kind': 'after', 'items': [(title index, (18, 0))], 'unknown': n}:
    open tasks the owner names and the hour they are to be done after. Names that match no task are counted, not guessed."""
    f = fold(text)
    ms = list(AFTER.finditer(f))
    if "?" in f or not ms or NAG.search(f) or MORE_ADD.match(f) or ADD.match(f) or find_date(text, date(2000, 1, 3)):
        return None
    items, unknown, prev = [], 0, 0
    for m in ms:
        chunk = f[prev:m.end()]
        seg = [w for w in re.findall(r"\w+", f[prev:m.start()]) if w not in FILLER and w not in {"aksam", "saat", "sabah", "ve", "ile"}]
        prev = m.end()
        at = (_hour(chunk, int(m[1])), int(m[2] or 0))
        hit = [i for i, t in enumerate(titles) if _kept(seg, t)]
        items += [(i, at) for i in hit]
        unknown += 0 if hit else 1
    return {"kind": "after", "items": items, "unknown": unknown} if items else None


def parse_after_groups(text: str) -> list[dict] | None:
    """'cop ve acme aksam 6dan sonra hue'lar da aksam 6dan sonra' -> [{'words': ['cop', 'acme'], 'at': (18, 0)}, ...]: the names
    of each 'N-dan sonra' group, matched by the caller against tasks AND today's calendar events."""
    f = fold(text)
    ms = list(AFTER.finditer(f))
    if "?" in f or not ms or NAG.search(f) or MORE_ADD.match(f) or ADD.match(f) or find_date(text, date(2000, 1, 3)):
        return None
    out, prev = [], 0
    for m in ms:
        chunk = f[prev:m.end()]
        seg = re.sub(r"['’]\w+", "", f[prev:m.start()])                       # drop suffixes: hue'lar -> hue
        words = [w for w in re.findall(r"\w+", seg) if w not in FILLER and w not in {"aksam", "saat", "sabah", "ve", "ile"}]
        prev = m.end()
        out.append({"words": words, "at": (_hour(chunk, int(m[1])), int(m[2] or 0))})
    return out


def words_match(words: list[str], title: str) -> bool:
    return _kept(words, title)
