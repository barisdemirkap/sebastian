"""Reads of 'what is my plan today' and 'why are my tasks not in it', with a small typo-tolerant vocabulary.

Typos: only the key words diger / gorev / bugun / plan are repaired, by edit distance 1 (a substitution or an adjacent swap
on the word's first letters, so 'doger' -> 'diger', 'gorec' -> 'gorev'); never an insertion or deletion, and a blocklist keeps real
words ('deger', 'dugun', 'gorsel', 'plaj') from being rewritten. Dropped diacritics are already handled by `fold`."""
from __future__ import annotations
import re

from .textutil import fold

VOCAB = ("diger", "gorev", "bugun", "plan")
BLOCK = ("deger", "dugun", "gorsel", "goren", "gorus", "gorun", "gorme", "gorec", "bugul", "dizer", "dever")
SUBST_MIN = 5                # a 4-letter word ('plan') only tolerates an adjacent swap: 'plaj', 'flan', 'klan' are real words


def _edit1(a: str, b: str) -> bool:
    """Same length, differing by exactly one substitution, or by one adjacent swap."""
    if len(a) != len(b) or a == b:
        return False
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    if len(diff) == 1:
        return True
    return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]


def _swap_only(a: str, b: str) -> bool:
    diff = [i for i in range(len(a)) if a[i] != b[i]] if len(a) == len(b) else []
    return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]


def _fix_token(tok: str) -> str:
    if len(tok) < 4 or tok.startswith(BLOCK) or any(tok.startswith(v) for v in VOCAB):
        return tok
    for v in VOCAB:
        head = tok[:len(v)]
        if len(head) == len(v) and (_edit1(head, v) if len(v) >= SUBST_MIN else _swap_only(head, v)):
            return v + tok[len(v):]
    return tok


def fix(text: str) -> str:
    """The folded text with typos of the key words repaired ('ayrica doger gorevlerim' -> 'ayrica diger gorevlerim')."""
    return " ".join(_fix_token(w) for w in re.findall(r"[^\s]+", fold(text)))


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", fix(text))


NOT_A_READ = re.compile(r"\b(ekle\w*|sil\w*|iptal|ertele\w*|tasi\w*|kaydir\w*|degistir\w*|yap|yapalim|olustur\w*|koy\w*|hatirlat\w*|yarin\w*|hafta\w*|dun)\b|\d")
ASK_WORD = {"ne", "neler", "nasil", "nedir", "goster", "soyle", "ver", "var", "bakalim", "nelerim", "neyim", "ozet", "ozetle"}
TASK_REFERENCE = {"gorev", "gorevlerim", "gorevler", "gorevim", "gorevlerimi", "gorevleri", "isler", "islerim", "listem", "yapilacaklar", "yapilacaklarim"}
MISSING = {"yok", "gozukmuyor", "gorunmuyor", "eksik", "yazmiyor", "almiyor", "gelmiyor", "cikmiyor", "yazmamissin", "eklememissin", "yazmamis", "koymamissin"}
WHY = {"neden", "niye", "nicin"}


PLAN_VOCAB = {"guncel", "simdiki", "son", "plan", "program", "nasil", "ne", "neler", "nedir", "goster", "soyle", "ver", "var", "bakalim",
              "ozet", "ayrica", "lutfen", "bana", "benim", "peki", "hadi", "de", "da", "mi", "mu", "gundem", "yapiyorum", "yapiyoruz",
              "simdi", "nelerim", "bugun", "gunun", "bugunun", "bugunku", "gununun", "ozetle", "ogrenebilir", "miyim"}


def _known(x: str) -> bool:
    return x in PLAN_VOCAB or x.startswith(("plan", "program", "bugun"))


def is_plan_read(text: str) -> bool:
    """'bugunku plan', 'guncel bugun planim nasil', 'bugun ne var', 'planim ne': a read of today, which holds events AND tasks."""
    f = fix(text).strip(" .!?,;")
    if not f or NOT_A_READ.search(f) or len(f.split()) > 7 or "gorev" in f:
        return False
    w = re.findall(r"[a-z]+", f)
    if not all(_known(x) for x in w):
        return False
    today = any(x.startswith("bugun") for x in w)
    plan = any(x.startswith("plan") or x in ("programim", "programi", "program") for x in w)
    if plan and (today or any(x in ASK_WORD for x in w) or len(w) <= 2):
        return True
    return False                 # 'bugun ne var' has no plan word: rules and model read it as list_events, which holds the tasks too


def is_missing_tasks_question(text: str) -> bool:
    """'ayrica diger gorevlerim neden bugunku planda yok?': a why-question about tasks missing from the plan."""
    w = _words(text)
    if not w or len(w) > 12:
        return False
    return (any(x in WHY or x.startswith("nicin") for x in w) and any(x.startswith("gorev") or x in TASK_REFERENCE for x in w)
            and any(x.startswith("plan") or x.startswith("bugun") or x.startswith("program") for x in w)
            and any(x in MISSING for x in w))
