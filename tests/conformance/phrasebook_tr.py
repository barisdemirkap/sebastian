"""Turkish phrasebook for the conversation conformance suite (bot-agnostic: no bot imports; ported from Acme, Sebastian stays standalone).

Plain data plus a deterministic variant generator. The same lists are used by the invariants (to classify a user
message) and by the property-style tests (to fuzz a bot). Never uses real people or chats. This file is a test fixture: Turkish test data is allowed here (ADR 0008)."""
from __future__ import annotations
import random

AFFIRM_BASE = [
    "evet", "Evet", "EVET", "evet.", "evet!", "Evet kaydet", "evet kaydet", "tamam kaydet", "Tamam, kaydet",
    "olur", "olur yaz", "olur kaydet", "onaylıyorum", "onayliyorum", "Onaylıyorum.", "ok", "OK", "okey",
    "tamam", "Tamam", "tamamdır", "tamamdir", "kaydedin", "kaydet", "kaydet lütfen", "lütfen kaydet",
    "evet lütfen", "evet tamam", "evet olur", "tamam evet", "peki kaydet", "hemen kaydet", "doğru", "dogru",
    "aynen", "aynen öyle", "kabul", "evet kardeş", "evet abi", "he", "yes", "evet bunu kaydet",
    "evet hepsini kaydet", "tamam hepsini kaydet", "evet doğru", "evet aynen", "evet yaz", "yaz", "tamam yaz",
    "kaydedelim", "kaydetsin", "evet kaydedin", "onay", "onayla", "tmm", "evt", "tamamm", "evveet", "kaydettt",
    "oluuur", "okk", "👍", "✅", "👌", "evet 👍", "tamam ✅", "evet kaydet 👍", "evet, kaydet", "Evet, tamam.",
    "tamam, olur", "evet evet", "olur tamam", "EVET KAYDET", "TAMAM KAYDET", "ONAYLIYORUM", "evet kaydet!!",
    "evet…", "tamam.", "olur!", "evet kaydet.", "kaydet.", "tamam kaydet lütfen", "evet lütfen kaydedin",
    "ok kaydet", "ok tamam", "peki evet", "evet peki", "olur hemen yaz", "kaydedin lütfen", "onaylıyorum kaydet",
    "iyi kaydet", "evet iyi", "evet tamamdır", "tamamdır kaydet",
]
# Whisper-style transcripts: lowercase, trailing period, no emoji
WHISPER_AFFIRM = ["evet.", "tamam kaydet.", "olur.", "onaylıyorum.", "evet kaydet.", "tamam.", "kaydet."]

NEGATE_BASE = [
    "hayır", "Hayır", "hayir", "HAYIR", "hayır.", "vazgeç", "vazgeçtim", "vazgec", "Vazgeç!", "iptal",
    "iptal et", "İPTAL", "istemiyorum", "olmaz", "yok", "bırak", "birak", "boşver", "bosver", "dur", "no",
    "hayır istemiyorum", "hayır vazgeç", "hayır iptal", "vazgeçiyorum", "kaydetme", "kaydetmeyin", "yazma",
    "hayır kaydetme", "iptal lütfen", "hayır lütfen", "gerek yok", "şimdilik gerek yok", "hayır abi", "hayır kardeş",
    "👎", "❌", "hayır ❌", "/iptal", "hayır hayır", "olmaz hayır", "kalsın", "yok kalsın",
]
# an edit of the shown draft: never saves, never destroys the preview without replacing it by a new one
EDIT_BASE = [
    "evet ama saat 16:00 olsun", "hayır, saati 17:00 yap", "evet ama yarın olsun", "bu şekilde değil", "yanlış",
    "olmadı", "saati 16:00 yap", "evet ama hepsi için 1 saat önce hatırlat", "evet ama başlık Toplantı olsun",
    "hayır saati 15:00 yap", "tamam ama tarihi 25 Kasım yap", "kaydet ama saat 14:00", "olur ama saati 18:00 yap",
    "evet fakat yarın olsun", "evet 16:00", "hepsini bir gün ertele",
]
# not a confirmation, not a cancellation, not an edit: must never touch a pending preview
CHITCHAT = [
    "merhaba", "selam", "nasılsın", "teşekkürler", "sağ ol", "günaydın", "iyi akşamlar", "orada mısın",
    "bir saniye", "bekle biraz", "hmm", "ıı", "şey", "acaba", "bilmiyorum", "sonra bakarım", "kardeş napıyorsun",
]
READ_QUESTIONS = [
    "hepsini kaydettin mi", "bugün ne var", "yarın ne var", "bu hafta ne var", "kaç etkinlik var", "doğum günleri",
    "/bugun", "/yarin", "/hafta", "/yardim", "/komutlar", "/dogumgunleri",
]
GIBBERISH = ["asdfgh", "qwerty", "zzz", "???", "...", "!!!", "xjkqz", "lkjhg", "aaaaaaaa", "😀", "🤷", "😂😂",
             "🙂", "-", "ğğğ", "mmmm", "hh", ";)", ":)"]
FRUSTRATION = ["anlamadın", "ulan", "embesil", "zaten yazdım", "dedim ya", "tekrar tekrar", "yine aynı",
               "olmuyor", "yine mi aynı soru", "anlamıyorsun"]

_DIACRITICS = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


def tr_upper(s: str) -> str:
    return s.replace("i", "İ").replace("ı", "I").upper()


def tr_lower(s: str) -> str:
    return s.replace("İ", "i").replace("I", "ı").lower()


def variants(phrase: str, rng: random.Random, n: int = 4) -> list[str]:
    """Deterministic spelling variants of `phrase`: missing diacritics, Turkish/ASCII case, punctuation, trailing
    spaces, an emoji or repeated vowel. The caller supplies a seeded `random.Random`."""
    out = [phrase.translate(_DIACRITICS), tr_upper(phrase), tr_lower(phrase), phrase + "  ", " " + phrase]
    out.append(phrase.translate(_DIACRITICS).upper())
    extras = [".", "!", "!!", "...", " :)", " 🙏", ","]
    out.append(phrase.rstrip(".!") + rng.choice(extras))
    words = phrase.split(" ")
    out.append(" ".join(w.capitalize() if rng.random() < .5 else w for w in words))
    seen, res = set(), []
    for v in out:
        if v.strip() and v != phrase and v not in seen:
            seen.add(v)
            res.append(v)
    rng.shuffle(res)
    return res[:n]


def fuzz(base: list[str], seed: int, n: int = 4) -> list[str]:
    """Every base phrase plus `n` variants each, in a stable order for a given seed."""
    rng = random.Random(seed)
    out: list[str] = []
    for p in base:
        out.append(p)
        out += variants(p, rng, n)
    return list(dict.fromkeys(out))


AFFIRMATIONS = fuzz(AFFIRM_BASE, seed=20261118)
NEGATIONS = fuzz(NEGATE_BASE, seed=20261119)
EDITS = fuzz(EDIT_BASE, seed=20261120, n=2)
NEUTRAL = CHITCHAT + READ_QUESTIONS + GIBBERISH + FRUSTRATION


def _key(s: str) -> str:
    return " ".join(tr_lower(s).translate(_DIACRITICS).strip().split())


_AFF_KEYS = {_key(s) for s in AFFIRMATIONS}
_NEG_KEYS = {_key(s) for s in NEGATIONS}
_EDIT_KEYS = {_key(s) for s in EDITS}


def classify(text: str) -> str:
    """'confirm' | 'cancel' | 'edit' | 'neutral' (everything else) for a user message, by exact phrasebook match."""
    k = _key(text)
    if k in _AFF_KEYS:
        return "confirm"
    if k in _NEG_KEYS:
        return "cancel"
    if k in _EDIT_KEYS:
        return "edit"
    return "neutral"


# I10: a bot message that offers a yes/no answer ("...ister misin?", "...ekleyeyim mi?", "(evet / hayır)").
# Deliberately narrow: polite requests such as "yazar mısın?" are not yes/no offers.
import re as _re
YES_NO_QUESTION = _re.compile(
    r"ister\s*mis(?:in|iniz)|(?:eyim|ayım|ayim)\s+m[iıuü]\b|(?:sem|sam)\s+m[iıuü]\b|(?:elim|alım|alim)\s+m[iıuü]\b|"
    r"(?:sin|sın|sun|sün)\s+m[iıuü]\s*\?|olur\s+mu\b|emin\s+misin|\(\s*evet\s*/\s*hay[ıi]r|evet\s+sil", _re.I)
