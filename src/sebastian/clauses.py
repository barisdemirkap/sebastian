"""Clause interpreter (training loop): turns one owner message into per-clause statements about EXISTING items.

Pure: no store, no model, no calendar. `split` cuts a message into clauses, `classify` reads one clause into
{'kind', 'subject', 'day', 'time', ...}; the assistant resolves each subject against the open tasks and the calendar
(a reference to an existing item is resolved, never created again) and applies the statement:

- done: closes the item (never proposes deleting it)
- defer: 'bu yarina', 'yarina kalsin', 'cumartesiye tasi' reschedule the item (due day for a task, a move preview for an event)
- partial: 'X topladim sadece Y kaldi' closes X and keeps Y
- remind / delete / keep / plan / forgot / read_remaining / read_day / info

`interpret` returns None when any clause cannot be read, so the older flows keep handling those messages."""
from __future__ import annotations
import difflib
import re
from datetime import date, timedelta

from .textutil import fold
from .timeparse import find_date

DONE_WORDS = {"bitti", "bitt", "bitirdim", "bitirdik", "yapildi", "yaptim", "yaptik", "tamamlandi", "tamamladim", "tamamladik", "halloldu", "hallettim",
              "hallolmus", "oldu", "odendi", "odedim", "edildi", "tamamlanmis", "bitmis", "gonderildi", "yazildi", "hazirlandi", "temizlendi",
              "toplandi", "halledildi", "hazir", "degisti", "kondu", "tamamdir", "oldu", "tamamdir", "kurudu", "kurumus", "bitirildi", "yikandi", "yapilmis", "tamamlanmistir"}
DONE_RX = re.compile(r"^[a-z]{2,}(?:dim|dum|tim|tum|dik|duk|tik|tuk)$|^[a-z]{2,}(?:ildi|indi|undu|uldu|andi|endi|ilmis|ulmus|anmis|enmis|inmis|unmus)$")
NOT_DONE = {"baktim", "yetismedi", "demistim", "demistik", "tukendi", "geldi", "dedim", "unuttum", "sordum", "bildim", "istedim", "kaldim", "yapamadim", "yetistiremedim", "unuttuk", "yapamadik"}
NEGATED = re.compile(r"m[dt][iu][mk]$|m[ae](?:dim|dik|di|yacag\w*|yecek\w*)$|m[ae]m$|miyor\w*$|ilmedi$|ulmadi$")
FUTURE = re.compile(r"^[a-z]+(?:acak|ecek|acagim|ecegim|acagiz|ecegiz|acaksin)$")
DEFER_VERBS = {"kalsin", "kaldi", "kalacak", "kalmis", "birak", "birakalim", "birakiyorum", "birakin", "tasi", "tasiyalim", "tasiyorum", "at", "atalim",
               "ertelendi", "ertelenmis", "ertelensin", "al", "ertele", "erteleyelim", "ertelesek", "erteliyorum", "olsun", "kaydir", "kaydiralim", "kaysin", "kaydirsin", "kaysinlar"}
WEEKDAYS = {"pazartesi": 0, "sali": 1, "carsamba": 2, "persembe": 3, "cuma": 4, "cumartesi": 5, "pazar": 6}
DAY_SUFFIX = {"", "ye", "ya", "e", "a", "yi", "gunu", "gunune", "gunun", "gununden", "gunde"}
TODAY_WORDS = {"bugun", "bugune", "bugunku", "bugunun"}
FILLER = {"olmaz", "olmuyor", "olamaz", "tekrar", "arada", "ama", "vaktim", "vakit", "geri", "de", "da", "ki", "ya", "ama", "artik", "zaten", "lutfen", "hala", "halen", "sadece", "yalnizca", "bunu", "onu", "bu", "ben", "evet", "peki",
          "tamam", "ve", "ile", "icin", "bir", "gibi", "saat", "yine", "ayrica", "simdi", "sonra", "once", "daha", "ona", "gerek", "yok", "hemen", "gunu", "gunune", "kadar", "bugunluk", "son", "olarak", "be", "abi", "sonunda", "nihayet", "birden", "beraber",  "vallahi", "yaw", "yav", "ha", "ayri",  "bi", "dakika", "aslinda", "valla", "yani", "iste", "hani", "aa", "lan", "ikisi", "ikisini", "ikiside", "bana", "benim", "bize", "olan", "olani", "olanlari", "mutlaka", "lazim"}
DAYPART = {"aksam", "aksama", "sabah", "sabaha", "ogleden", "ogle", "gece", "geceye", "ikindi", "yarin", "yarina", "yarinki", "obur", "haftaya"}
OTHERS = {"digerleri", "digerlerini", "gerisi", "gerisini", "kalanlar", "kalanlari", "kalani"}
ALL = {"hepsi", "hepsini", "tumu", "tumunu", "bugunkuler", "bugunkulerin"}
HEADER = re.compile(r"^\s*(?:pzt|sal|car|per|cum|cmt|paz)\s+\d{1,2}\s+[a-z]{3}\s+\d{1,2}[:.]\d{2}\s+", re.I)
CUE_VERB = re.compile(r"^(?:hatirlat\w*|kaldir\w*|sil|silin|iptal\w*|yapacag\w+|yapariz|yaparim|belirt\w*|soyle\w*|goster\w*|yazar\w*)$")
ABBR = {"yrn": "yarina", "yarn": "yarina", "yarina": "yarina"}
INFO_END = {"kalmadi", "kalmamis", "gitti", "gitmis", "hazirmis", "gelmis", "ulasti", "ulasmis"}
STATE_END = {"kaysin", "kaydirsin", "kalmadi", "kalmamis", "kaldi", "kalsin", "acik", "duruyor", "dursun", "yapamayacagim", "yapamam", "yetismedi"}


def _w(tok: str) -> str:
    """A folded token without punctuation and without an apostrophe suffix ('hue'lar' -> 'hue')."""
    w = re.sub(r"['’]\w*$", "", fold(tok).strip("'’\".,!?:;()[]"))
    w = re.sub(r"(.)\1{2,}", r"\1\1", w)
    w = re.sub(r"([a-z])([dt])m$", r"\1\2im", w) if len(w) > 5 else w          # 'suladm' -> 'suladim' (a dropped vowel)                    # 'yaaaa' -> 'yaa', 'tamaaam' -> 'tamaam'
    w = {"yaa": "ya", "yaaa": "ya", "bee": "be"}.get(w, w)
    return ABBR.get(w, w)


NOUN_ENDINGS = ("stik", "matik", "netik", "otik", "ratik", "lastik", "mantik", "klasik", "politik", "taktik", "praktik", "gotik", "tuk", "ritik")


NOUNS_LIKE_DONE = {"vaktim", "yardim", "kadim", "ortum", "yurdum", "sertum"}           # 'vaktim yok' = 'I have no time', not 'I did it'


def is_done_word(w: str) -> bool:
    if re.search(r"[ae]c[ae]kt[iu]?[mk]$", w) or w in NOUNS_LIKE_DONE or w.endswith(NOUN_ENDINGS) and w not in DONE_WORDS:
        return False                                   # 'lastik', 'plastik' are nouns that happen to end like 'yaptik'
    if w.startswith("ertel") or w in NOT_DONE or NEGATED.search(w) or re.search(r"nt[iu][mk]$", w):        # 'toplantim' is a noun ('my meeting'), not a verb
        return False
    return w in DONE_WORDS or bool(DONE_RX.match(w))


DELETE_WORDS = {"sil", "silin", "silelim", "silebilirsin", "silsene", "siliyorum", "siliniz", "iptal"}


def is_delete_word(w: str) -> bool:
    return w in DELETE_WORDS or w.startswith("kaldir")


def _is_not_done(w: str) -> bool:
    """'yapamadim', 'yikamadim', 'unuttum', 'yetisemedi' (third person): said it was NOT done."""
    return bool(re.search(r"m[ae]d[i\u0131u\u00fc][mk]$|^(?:bit|yap|ol|hazirla)m[ae]di$|^[a-z]{3,}[ae]m[ae]di$", w)) or w in NOT_DONE - {"baktim", "demistim", "demistik", "dedim", "sordum", "bildim", "istedim", "kaldim", "tukendi", "geldi"}


def _weekday(w: str) -> int | None:
    for name in sorted(WEEKDAYS, key=len, reverse=True):
        if w.startswith(name) and w[len(name):] in DAY_SUFFIX:
            return WEEKDAYS[name]
    return None


def is_day_cue(w: str) -> bool:
    return w in {"yarina", "yarin", "yarinki", "bugune", "bugun", "obur", "haftaya"} or _weekday(w) is not None


def is_defer_verb(w: str) -> bool:
    return w in DEFER_VERBS or (len(w) >= 5 and any(difflib.SequenceMatcher(None, w, v).ratio() > .84 for v in ("kalsin", "birakalim", "tasiyalim", "ertele")))


def _is_dative_day(w: str) -> bool:
    """'yarina', 'bugune', 'cumartesiye', 'pazartesi gunune': a day the item is moved TO."""
    return w in {"yarina", "bugune"} or (_weekday(w) is not None and w not in WEEKDAYS and w[-1:] in "ea" and not w.endswith(("gunu", "gunun")))


def _is_future(w: str) -> bool:
    return bool(FUTURE.match(w))


def _is_aorist(w: str) -> bool:
    """'duzenlerim', 'yikarim': a plan in the broad present tense; only read as one when the clause names a day."""
    return bool(re.fullmatch(r"[a-z]{2,}(?:ar|er|ir|ur)(?:im|iz)", w))


def _run_end(ws: list[str], i: int) -> int:
    """Index (exclusive) where a clause ends after token i, or 0 when token i does not end one."""
    w = ws[i]
    nxt = ws[i + 1] if i + 1 < len(ws) else ""
    if w == "var" and i + 1 < len(ws):
        return i + 1
    if w in {"tamam", "tamamdir"} and i > 0 and ws[i - 1] not in FILLER - {"da", "de"} and i + 1 < len(ws):
        return i + 1                           # 'kart yenilendi lastikler tamam balkon yarina'
    if w in INFO_END:
        return i + 1
    if is_done_word(w):
        if w == "hazir" and nxt.startswith("ol"):
            return 0                           # 'hazir olalim' = 'let us be ready', not 'ready'
        if nxt in {"mi", "mu", "mi", "mu"} or nxt.startswith(("mi?", "mu?")):
            return i + 2                       # 'kitaplari topladim mi?' is a question, one clause
        return 0 if nxt in {"sadece", "yalnizca", "yalniz"} or (w == "tamam" and nxt) else i + 1
    if _is_not_done(w):
        return i + 1
    if _is_dative_day(w):
        j = i + 1
        while j < len(ws) and (is_defer_verb(ws[j]) or ws[j] in {"bu", "lutfen"} and j + 1 < len(ws) and (_is_dative_day(ws[j + 1]) or ws[j + 1] in DEFER_VERBS)
                               or _is_dative_day(ws[j]) or ws[j] in {"gunune"}):
            j += 1
        return j
    if w == "iptal" and nxt.startswith(("et", "edil")):
        return 0                               # 'aboneligi iptal ettim': the verb comes next
    if w.startswith("hatirlat") or is_delete_word(w) or w in STATE_END or (len(w) >= 5 and is_defer_verb(w) and w not in DEFER_VERBS):
        return i + 1
    if re.search(r"m[ae]y[ae]c[ae]k$", w) and is_delete_word(nxt):
        return 0                               # 'artik olmayacak iptal': one clause, the cancellation
    if _is_future(w):
        j = i + 1
        while j < len(ws) and (ws[j] in DAYPART or ws[j] in TODAY_WORDS or _weekday(ws[j]) is not None or re.fullmatch(r"\d{1,2}(?:[.:]\d{2})?\w*", ws[j]) or ws[j] in {"saat", "sonra", "gibi"}):
            j += 1
        if j > i + 1 and "var" in ws[j:] and any(re.fullmatch(r"\d.*", w) for w in ws[i + 1:j]):
            return i + 1                       # 'X cikartacagim bugun 10da Y toplantim var': the time belongs to the next clause
        return j
    return 0


def split(text: str) -> list[str]:
    """Pieces of a message, in order, each ending at its own verb: 'camasir bitti utu yarina kaldi' -> two."""
    out: list[str] = []
    for seg in re.split(r"[,;\n]+|\s[-–—]\s|(?<!\d)\.(?!\d)", text):
        toks = HEADER.sub("", seg.strip()).split()
        ws = [_w(t) for t in toks]
        cur: list[str] = []
        i = 0
        while i < len(toks):
            cur.append(toks[i])
            end = _run_end(ws, i)
            if end:
                cur += toks[i + 1:end]
                out.append(" ".join(cur))
                cur, i = [], end
                continue
            i += 1
        if cur:
            out.append(" ".join(cur))
    keep = lambda q: len(q) > 1 and not all(_w(t) in FILLER or not _w(t) for t in q.split())      # noqa: E731
    return [p for p in (q.strip(" .,;-") for q in out) if keep(p)]


def _time(f: str) -> tuple[int, int] | None:
    """'bu aksam 10 gibi' -> (22, 0); 'saat 7.30' -> (7, 30); None when the clause carries no clock time."""
    m = re.search(r"(?:\b(aksam|ogleden sonra|ikindi|gece|sabah)\s+)?\b(\d{1,2})(?:[.:](\d{2}))?\s*(?:gibi|'?(?:[dt][ae]n?|[yn]?[ae]))?(?=\s|$)", f)
    if not m or (not m[1] and not m[3] and not re.search(r"\bsaat\b|gibi|\b\d{1,2}'?(?:[dt][ae]n?|[yn]?[ae])\b", f)):
        return None
    h, mi = int(m[2]), int(m[3] or 0)
    if h > 23 or mi > 59:
        return None
    return (h + 12 if m[1] in ("aksam", "ogleden sonra", "ikindi", "gece") and h < 12 else h), mi


def _day(ws: list[str], today: date) -> date | None:
    if "yarina" not in ws and (dest := [w for w in ws if _weekday(w) is not None and _is_dative_day(w)]) and (found := find_date(dest[0], today)):
        return found[0]                                 # 'yarin gidemem cumartesiye birak': 'yarin' is the day it cannot be, the dative day is where it goes
    if any(w in {"yarina", "yarin", "yarinki"} for w in ws):
        return today + timedelta(days=1)
    cue = " ".join(w for w in ws if _weekday(w) is not None or w in {"haftaya"})
    if "bugune" not in ws and cue and (found := find_date(cue, today)):
        return found[0]                                 # 'bugun olmaz cumaya': the day it moves TO wins over the day it cannot be
    if any(w in {"bugune", "bugun"} for w in ws):
        return today
    if cue and (found := find_date(cue, today)):
        return found[0]
    return None


JOINERS = {"ve", "ile", "icin"}
PARTS = {"aksam", "sabah", "gece", "ogle", "ikindi"}


def _subject(toks: list[str]) -> str:
    """The words that name the item: cue words (day, time, verbs, fillers) dropped; 've'/'icin' kept between two kept words; a part of
    the day ('sabah islerini') kept when it is part of the name and not a time ('aksam 10 gibi')."""
    ws = [_w(t) for t in toks]
    drop = []
    phoned = any(w.startswith("ac") and is_done_word(w) for w in ws)           # 'anneme telefon actim' = 'Annemi ara' done
    for i, w in enumerate(ws):
        nxt = ws[i + 1] if i + 1 < len(ws) else ""
        part_is_name = w in PARTS and nxt and not re.fullmatch(r"\d.*|gibi|saat", nxt) and not (_is_future(nxt) or is_done_word(nxt) or CUE_VERB.match(nxt)) \
            and (i == 0 or ws[i - 1] != "bu")
        drop.append(not w or (phoned and w == "telefon") or (w in FILLER and w not in JOINERS) or is_defer_verb(w) or (w in DAYPART and not part_is_name) or w in TODAY_WORDS
                    or is_done_word(w) or _is_not_done(w) or is_day_cue(w) or w in OTHERS or w in ALL or w in STATE_END or bool(re.fullmatch(r"\d{1,2}(?:[.:]\d{2})?\w*", w))
                    or bool(CUE_VERB.match(w)))
    keep = []
    for i, t in enumerate(toks):
        if drop[i]:
            continue
        if ws[i] in JOINERS and not (keep and i + 1 < len(toks) and not drop[i + 1]):
            continue
        keep.append(t.strip("'’\""))
    return " ".join(keep).strip(" -:")


def classify(seg: str, today: date, carry_day: date | None = None) -> dict | None:
    """One clause -> {'kind', 'subject', 'day', 'time', 'scope', 'rest', 'raw'}; None when the clause is not understood."""
    raw = re.sub(r"(?i)\bpazar\s+(?=yeri|alisveris|pazari)", "pazar", seg.strip())         # 'pazar yeri' is a place, not Sunday
    f = fold(raw)
    toks = raw.split()
    ws = [_w(t) for t in toks]
    if "kalan" in ws and ("geri" in ws or any(_is_dative_day(w) for w in ws)):
        ws = [w for w in ws if w not in {"geri", "kalan"}] + ["kalanlari"]           # 'geri kalan yarina' = the others
        toks = [t for t in toks if _w(t) not in {"geri", "kalan"}] + ["kalanlari"]
    scope = "others" if any(w in OTHERS for w in ws) else "all" if any(w in ALL for w in ws) else None
    day, tm = _day(ws, today), _time(f)
    base = {"raw": raw, "subject": "", "day": day, "time": tm, "scope": scope, "rest": ""}
    if "?" in raw:
        return dict(base, kind="other")
    # reads of what is left today and of tomorrow's plan
    if re.search(r"\bne(?:ler)? kaldi\b|\bkalan\w*\b", f) and re.search(r"belirt|soyle|goster|\bver\b|oku|\bne(?:ler)? kaldi\b", f):
        out = dict(base, kind="read_remaining")
        if re.search(r"\byarin\w*\s+(?:plan|program)", f):
            out["also"] = [dict(base, kind="read_day", day=today + timedelta(days=1))]
        return out
    if re.search(r"\b(?:yapilacak|yapilmasi gereken)\b.*\bneler\b|\bneler\b.*\b(?:yapilacak|yapilmasi gereken)\b", f) and not re.search(r"\byarin", f):
        return dict(base, kind="read_remaining")
    if re.search(r"\byarin\w*\s+(?:ne|neler)\s+var\b", f):
        return dict(base, kind="read_day", day=today + timedelta(days=1))
    if re.search(r"\byarin\w*\s+(?:plan|program)\w*", f) and (re.search(r"belirt|soyle|goster|oku|nasil|neler|ne var", f) or re.fullmatch(r"(?:bir de |ayrica |de )?yarin\w*\s+(?:plan|program)\w*", f)):
        return dict(base, kind="read_day", day=today + timedelta(days=1))
    done = (len(ws) > 1 and ws[-1] in {"tamam", "tamamdir"}) or any(is_done_word(w) and not (w == "alindi" and tm) and not (w == "hazir" and ws[i + 1:i + 2] and ws[i + 1].startswith("ol")) for i, w in enumerate(ws)) or bool(scope and any(w in {"tamam", "ok", "tm"} for w in ws))
    if any(w in {"yapamayacagim", "yapamam"} for w in ws) and not done:
        return dict(base, kind="info")
    if "vaktim" in ws and ws[-1] in {"kalmadi", "yok", "olmadi"} and not done:
        return dict(base, kind="forgot", subject=_subject(toks))               # 'icin vaktim kalmadi': not done, so it moves
    if re.search(r"\bgerek (?:kalmadi|kalmamis|yok|yoktu)\b", f) and not done and _subject(toks):
        return dict(base, kind="delete", subject=_subject(toks))        # 'X'e gerek kalmadi': no longer needed, so the removal is previewed
    if ws and ws[-1] in INFO_END and not done:
        return dict(base, kind="info")                    # 'dekont da gitti': says how things stand, nothing to do
    if ws[:1] in (["geriye"], ["kalan"]) and ws[-1:] == ["kaldi"]:
        return dict(base, kind="info")                    # 'geriye mektup kaldi': says what is left, nothing to do
    if done and (m := re.search(r"\b(?:sadece|yalnizca|yalniz)\s+(.+?)\s+kaldi\b", f)):
        n_before = len(f[:m.start()].split())
        n_rest = len(f[:m.start(1)].split())
        return dict(base, kind="partial", subject=_subject(toks[:n_before]), rest=" ".join(toks[n_rest:n_rest + len(m[1].split())]))
    if any(w.startswith("hatirlat") for w in ws):
        return dict(base, kind="remind", subject=_subject(toks))
    if any(_is_not_done(w) for w in ws):
        return dict(base, kind="forgot", subject=_subject(toks))
    if done:
        return dict(base, kind="done", subject=_subject(toks))
    if any(is_delete_word(w) for w in ws):
        return dict(base, kind="delete", subject=_subject(toks))
    if "aksama" in ws and any(is_defer_verb(w) for w in ws):
        return dict(base, kind="defer", subject=_subject(toks), day=today)
    if day and day != today and re.search(r"\b(?:olmaz|olmuyor|olamaz)\b", f) and _subject(toks):
        return dict(base, kind="defer", subject=_subject(toks))        # 'X bugun olmaz cuma': a bare weekday is where it goes
    dative = any(_is_dative_day(w) for w in ws)
    if dative or (day and any(is_defer_verb(w) for w in ws)):
        return dict(base, kind="defer", subject=_subject(toks), day=day or today + timedelta(days=1))
    if ws and (ws[-1] in {"kalsin", "dursun", "acik", "duruyor"} or (ws[-1] == "kaldi" and _subject(toks) and not (tm or day))):
        return dict(base, kind="keep", subject=_subject(toks))
    if any(_is_future(w) or (day and _is_aorist(w)) for w in ws):
        if scope:
            return dict(base, kind="defer", day=day or today + timedelta(days=1))              # 'kalanlari yarin yaparim'
        return dict(base, kind="plan", subject=_subject(toks), day=day or carry_day)
    if carry_day and _subject(toks):
        return dict(base, kind="plan", subject=_subject(toks), day=carry_day)
    if re.search(r"\blazim$", f) and not tm and not day:
        words = [t for t in toks[:-1] if _w(t) not in {"bir", "de", "tane", "daha"} or len(toks) < 3]
        return dict(base, kind="add", subject=" ".join(words))
    if not _subject(toks) and (tm or day):
        return dict(base, kind="info")
    return dict(base, kind="other")


def interpret(text: str, today: date) -> list[dict] | None:
    """Every clause of the message, classified. A 'bu yarina' said twice in a row after one item belongs to the next item too.
    None when any clause is unreadable."""
    out: list[dict] = []
    carry: date | None = None
    plan_day: date | None = None
    if len(text.split()) <= 6 and "?" not in text and re.search(r"\byapilacak\w*\b.*\bneler\b|\bneler\b.*\byapilacak", fold(text)) and "yarin" not in fold(text):
        return [dict(raw=text.strip(), subject="", day=today, time=None, scope=None, rest="", kind="read_remaining")]      # 'bugun yapilacak islerim neler'
    text = re.sub(r"(?i)\s(?:bu\s+)?\S+\s+de(?:g|\u011f)il,?\s+(\S+?)(?:ye|ya|e|a)?\b", lambda m: " " + m[1] + ("ye" if m[1][-1:] in "ie" else "ya" if m[1][-1:] in "a\u0131ou" else "a"), text)   # 'yoga bu yarina degil pazartesi'
    parts = split(text)
    for p in parts:
        extra = len(re.findall(r"\byarina\b", fold(p))) - 1
        c = classify(p, today, carry)
        if c is None:
            return None
        if c["kind"] == "other" and c["day"] and not c["time"] and len(parts) > 1 and "?" not in c["raw"] and (subj := _subject(c["raw"].split())):
            c.update(kind="defer", subject=subj)                  # 'sayac cumartesi' next to other statements: the item moves to that day
        if c["kind"] == "other" and not (c["day"] or c["time"]) and "?" not in c["raw"] and out and out[-1]["kind"] in ("defer", "done") and 1 <= len(c["raw"].split()) <= 3 \
                and _w(c["raw"].split()[-1]) in {"da", "de"} and (subj := _subject(c["raw"].split())):
            c = dict(c, kind=out[-1]["kind"], subject=subj, day=out[-1]["day"])         # 'blog yarina kalsin logo da': the same statement for one more item
        if c["kind"] == "other" and not (c["day"] or c["time"]) and "?" not in c["raw"] and out and out[-1]["kind"] == "forgot":
            c["kind"] = "info"                   # 'copu unuttum aile hekimindeyim': the rest only says why
        if c["kind"] == "forgot" and not c["subject"] and out and out[-1]["subject"] and out[-1]["kind"] in ("plan", "other", "info", "forgot"):
            c["subject"] = out.pop()["subject"]             # 'X bugun yaparim demistim ama olmadi': 'olmadi' is about X
        if out and out[-1]["kind"] == "forgot" and c["day"] and (not c["subject"] or (len(c["subject"].split()) == 1 and (_is_future(_w(c["subject"])) or _is_aorist(_w(c["subject"]))))) \
                and c["kind"] in ("defer", "info", "plan", "other"):
            out[-1].update(kind="defer", day=c["day"])         # 'raporu bitiremedim yarina kalsin': not finished, so it moves
            continue
        if c["kind"] == "plan" and not c["day"] and not c["time"] and plan_day:
            c["day"] = plan_day                       # 'bugun A duzenlenecek B yikanacak C toparlanacak': 'bugun' covers the plain plans that follow
        plan_day = c["day"] if c["kind"] == "plan" and not c["time"] and c["day"] else (plan_day if c["kind"] in ("plan", "done") else None)
        carry = c["day"] if c["kind"] == "defer" and extra > 0 else None
        if c["kind"] == "remind" and not c["subject"] and not c["time"] and out and out[-1]["kind"] in ("plan", "defer"):
            out[-1]["remind"] = True               # 'X cumartesi 12de ... bana onu hatirlat': the previewed event IS the reminder
            continue
        out.append(c)
        out += c.pop("also", [])
    return out or None


GENERIC_Q = {"isi", "isini", "isler", "isleri", "islerini"}
STOP = {"icin", "gibi", "kadar", "bugun", "yarin", "ile"}


_SOFT = str.maketrans("gbd", "kpt")


def _same_word(x: str, y: str) -> bool:
    n = 0
    while n < min(len(x), len(y)) and x[n] == y[n]:
        n += 1
    if len(x) >= 5 and len(y) >= 5 and (x, y) != (x.translate(_SOFT), y.translate(_SOFT)) and _same_word(x.translate(_SOFT), y.translate(_SOFT)):
        return True                                      # 'kopek' / 'kopegi': a softened final consonant
    sh, lo = sorted((x, y), key=len)
    st = re.sub(r"(?:lari|leri|larin|lerin|lar|ler|si|sini|yi|yu|ye|ya|nu|ni)$", "", sh)
    if len(st) >= 4 and len(sh) - len(st) >= 2 and lo.startswith(st):
        return True                                      # 'fotolari' / 'fotograflari'

    return n >= max(3, len(sh) - 2) or (len(sh) >= 4 and difflib.SequenceMatcher(None, sh, lo[:len(sh) + 2]).ratio() > .82)       # a typo'd stem still matches its longer inflected form


def _closeness(x: str, y: str) -> float:
    """0 when the words share no stem, else the shared prefix over the longer word ('toplanti'/'toplantisi' .8, 'toplanti'/'topla' .6)."""
    if not _same_word(x, y):
        return 0.0
    n = 0
    while n < min(len(x), len(y)) and x[n] == y[n]:
        n += 1
    return max(n / max(len(x), len(y)), .1)


def score(subject: str, title: str, ratio: float = .6) -> float:
    """0 when the subject does not name the title; else the summed closeness of its content words (higher = a closer name)."""
    subject = re.sub(r"\bdisci\w*", "dis hekimi", fold(subject))                    # 'disci' is the dentist: a name the title spells out
    q = [w for w in re.findall(r"\w+", fold(re.sub(r"['\u2019]\w*", "", subject))) if len(w) > 2 and w not in STOP]
    q = [w for w in q if w not in GENERIC_Q] or q
    q = [w for w in q if not FUTURE.match(w)] or q                  # 'dolap isi bitti': 'isi' only says 'the job'

    t = [w for w in re.findall(r"\w+", fold(title)) if len(w) > 2]
    best = [max((_closeness(w, v) for v in t), default=0.0) for w in q]
    hits = sum(1 for b in best if b > 0)
    exact = sum(.2 for w in q if w in re.findall(r"\w+", fold(re.sub(r"['\u2019]\w*", "", title))))
    apos = 0.0                                              # "ali'yi" names "Ali'yi ara", not "Ali'ye mesaj at"
    for m in re.finditer(r"(\w+)['\u2019](\w+)", fold(subject)):
        for tm in re.finditer(r"(\w+)['\u2019](\w+)", fold(title)):
            if tm[1] == m[1]:
                apos += .5 if tm[2] == m[2] else -.3
    short = [w for w in re.findall(r"\w+", fold(re.sub(r"['\u2019]\w*", "", subject))) if len(w) == 2 and w not in STOP and w in re.findall(r"\w+", fold(title))]      # 'su faturasi' vs 'Elektrik faturasi'
    return max(sum(best) + len(short) + exact + apos, .01) if q and hits >= max(1, -(-int(len(q) * ratio * 100) // 100)) else 0.0


def match(subject: str, title: str, ratio: float = .6) -> bool:
    return score(subject, title, ratio) > 0
