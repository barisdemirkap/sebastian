"""Small text helpers shared by the parsers: Turkish-aware folding and control-character cleaning."""
from __future__ import annotations
import difflib
import re

_TR = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def fold(s: str) -> str:
    """Lowercase and strip Turkish diacritics: 'Doğum Günü' -> 'dogum gunu', 'İş' -> 'is'."""
    return str(s).replace("İ", "i").replace("I", "ı").lower().translate(_TR)


def title_words(title: str) -> list[str]:
    return re.findall(r"\w+", fold(title))


def default_duration(title: str) -> int:
    """Estimated minutes for a task from its title: quick chores 15, long jobs/appointments 60-90, otherwise the
    30 minute default (owner request, msg 924). An explicit duration from the owner always wins."""
    from . import i18n
    words = title_words(title)
    for minutes, stems in i18n.DURATION_ESTIMATES:
        if any(w.startswith(stems) for w in words):
            return minutes
    return 30


def clean(s, limit: int) -> str:
    """Strip control characters and cap length (everything that goes into a prompt or reply)."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(s)).strip()[:limit]


def title_match(query: str, title: str) -> bool:
    """Fuzzy 'is this the thing the owner means': at least 60% of the query words (rounded up) start a word of the
    title, are started by one, or are close to one (Turkish suffixes, typos). Shared by events and tasks."""
    q = [w for w in re.findall(r"\w+", fold(query)) if len(w) > 1]
    toks = re.findall(r"\w+", fold(title))
    hit = sum(any(t.startswith(w) or (len(t) >= 3 and w.startswith(t)) or difflib.SequenceMatcher(None, w, t).ratio() > .82
                  for t in toks) for w in q)
    return bool(q) and hit >= -(-len(q) * 3 // 5)


def same_stem(x: str, y: str) -> bool:
    """Two folded words share a stem despite Turkish derivation and inflection: 'ode' ~ 'odeme' ~ 'odemesini' ~ 'odedim',
    'ceza' ~ 'cezalarini'. The shorter word (3+ letters) is a prefix of the other, or they share a prefix of all but two
    letters of the shorter one (at least 3)."""
    n = 0
    while n < min(len(x), len(y)) and x[n] == y[n]:
        n += 1
    m = min(len(x), len(y))
    return m >= 3 and (n == m or n >= max(3, m - 2))


def similar_title(a: str, b: str) -> bool:
    """Looser than `title_match`, for 'is this the same thing' on the same date and time (ADR 0015): every word of the
    shorter title shares a stem (a common prefix of at least 3 letters) with a word of the other, so 'Cop cikar',
    'Copu cikar' and 'Copleri cikaracagim' are one thing."""
    ta, tb = [w for w in re.findall(r"\w+", fold(a)) if len(w) > 1], [w for w in re.findall(r"\w+", fold(b)) if len(w) > 1]
    if not ta or not tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)

    def same(x: str, y: str) -> bool:
        n = 0
        while n < min(len(x), len(y)) and x[n] == y[n]:
            n += 1
        return n >= max(3, min(len(x), len(y)) - 2) or difflib.SequenceMatcher(None, x, y).ratio() > .82
    return all(any(same(w, v) for v in long_) for w in short)
