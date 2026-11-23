"""Deterministic dialogue helpers (ADR 0006). Pure: no store, no model, no Telegram.

`parse_confirm` decides whether a whole message is a plain yes or no for a pending preview. Anything with extra
content ('evet ama saat 16:00 olsun') is an edit, never a confirmation. Vocabulary lives in `i18n`."""
from __future__ import annotations
import copy
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta

from . import fuzzytime, i18n, timeparse
from .nl import MAX_NOTE, MAX_TITLE, SchemaError, _date, _int, _text, _time
from .textutil import default_duration, fold

_REPEAT = re.compile(r"(.)\1+")
_EMOJI_YES, _EMOJI_NO = ("👍", "✅", "👌", "🆗"), ("👎", "❌", "🚫")


def norm(text: str) -> str:
    """Folded, punctuation-free, repeat-collapsed ('Eveet!!' -> 'evet', 'ONAYLIYORUM.' -> 'onayliyorum')."""
    s = text
    for e in _EMOJI_YES:
        s = s.replace(e, " evet ")
    for e in _EMOJI_NO:
        s = s.replace(e, " hayir ")
    f = re.sub(r"[^a-z0-9\s]", " ", fold(s).replace("’", "'"))
    f = _REPEAT.sub(r"\1", f)
    return re.sub(r"\s+", " ", f).strip()


def words(text: str) -> list[str]:
    return norm(text).split()


def is_restating(text: str) -> bool:
    """'I said it already' wording ('dedim ya', 'yine', 'tekrar tekrar'): whole words, folded (ADR 0014)."""
    f = f" {norm(text)} "
    return any(f" {norm(p)} " in f for p in i18n.RESTATE_PHRASES)


def is_impatient(text: str) -> bool:
    """The whole message is one or more presence checks ('aloo', 'orda misin', 'cevap ver'); a merged burst of them
    (newlines are already blanks by then) counts too (ADR 0014)."""
    f = norm(text)
    return bool(f) and re.fullmatch(rf"(?:{i18n.IMPATIENT_LINE})(?: (?:{i18n.IMPATIENT_LINE}))*", f) is not None


def parse_confirm(text: str, delete: bool = False) -> str | None:
    """'yes' | 'no' | 'weak' | None. 'yes' only when the WHOLE message is an affirmation. For a delete preview a plain
    yes ('evet', 'tamam', '👍') is 'weak' (the caller asks for 'evet sil'); only 'sil', 'evet sil' or an explicit
    approval ('onayliyorum') is 'yes'."""
    w = words(text)
    if not w or len(w) > 5:
        return None
    yes = i18n.YES_CORE | i18n.YES_FILLER | (i18n.DELETE_WORDS if delete else set())
    if all(x in yes for x in w) and any(x in i18n.YES_CORE | (i18n.DELETE_WORDS if delete else set()) for x in w):
        if not delete:
            return "yes"
        return "yes" if any(x in i18n.DELETE_WORDS | i18n.EXPLICIT_APPROVAL for x in w) else "weak"
    if all(x in i18n.NO_CORE | i18n.NO_FILLER for x in w) and any(x in i18n.NO_CORE for x in w):
        return "no"
    return None


def split_edit(text: str) -> str | None:
    """'evet ama saati 16:00 yap' -> 'saati 16:00 yap'; 'hayir, 1 aralik olsun' -> '1 aralik olsun'. None when the text
    is not an edit of a shown draft. An edit never confirms."""
    f = fold(text)
    m = re.match(r"^\W*((?:[a-z0-9]+\W+){0,3}?)(" + "|".join(sorted(i18n.CONNECTORS)) + r")\b\W*(.*)$", f, re.S)
    if m and m[1].strip() and parse_confirm(m[1]) in ("yes", "no") and m[3].strip():
        return text[len(text) - len(m[3]):]
    m = re.match(r"^\W*(hayir)\b\W+(.+)$", f, re.S)
    if m:
        return text[len(text) - len(m[2]):]
    return None


# ---------------------------------------------------------------------------------------------------------------
# Operations on a pending add draft (ADR 0006). The model (or the rules) only PROPOSES operations; `validate_ops`
# checks them strictly and `apply_ops` applies them deterministically. The caller re-renders the preview in code.
MAX_OPS, MAX_ITEMS, MAX_SHIFT = 12, 30, 366
FIELDS = ("title", "date", "time", "duration_min", "reminder_min", "location", "notes")
READ_QUERIES = ("today", "tomorrow", "week", "birthdays", "upcoming")
OP_ALIASES = {"set_field": "set", "apply_to_all": "set", "remove_item": "remove", "add_items": "add",
              "shift_dates": "shift", "read_query": "read", "park": "new_request"}


def _items(v, n: int):
    if v in ("all", "cur", None):
        return "all"
    if isinstance(v, int) and not isinstance(v, bool):
        v = [v]
    if isinstance(v, list) and v and len(v) <= MAX_ITEMS and all(isinstance(x, int) and not isinstance(x, bool) for x in v):
        if not all(1 <= x <= n for x in v):
            raise SchemaError("item range")
        return sorted(set(v))
    raise SchemaError("item")


def _value(field: str, v, message: str, today: date):
    if field == "title":
        return _text(v, MAX_TITLE, required=True)
    if field == "date":
        r = _date(v, today, message)
        if r is None:
            raise SchemaError("date not said")
        return r
    if field == "time":
        r = _time(v, message)
        if r is None:
            raise SchemaError("time not said")
        return r
    if field == "duration_min":
        r = _int(v, 1, 1440, "duration")
    elif field == "reminder_min":
        r = _int(v, 0, 20160, "reminder")
    elif field in ("location", "notes"):
        return _text(v, 80 if field == "location" else MAX_NOTE)
    else:
        raise SchemaError("field")
    if r is None:
        raise SchemaError("value")
    return r


def _new_item(o: dict, message: str, today: date) -> dict:
    if not isinstance(o, dict):
        raise SchemaError("item")
    out = {"title": _text(o.get("title"), MAX_TITLE), "date": _date(o.get("date"), today, message),
           "time": _time(o.get("time"), message), "duration_min": _int(o.get("duration_min"), 1, 1440, "duration"),
           "all_day": bool(o.get("all_day")) is True and o.get("all_day") is True,
           "reminder_min": _int(o.get("reminder_min"), 0, 20160, "reminder"), "location": _text(o.get("location"), 80),
           "notes": _text(o.get("notes"), MAX_NOTE)}
    return out


def validate_ops(raw, message: str, today: date, n_items: int) -> list[dict]:
    """Strictly checked operations from the model; SchemaError on anything outside the schema. Dates and times must
    be ones the owner said in this message; a 'confirm' from a model is never an operation (see `apply_ops`)."""
    if not isinstance(raw, dict) or not isinstance(raw.get("ops"), list) or not 1 <= len(raw["ops"]) <= MAX_OPS:
        raise SchemaError("ops")
    ops = []
    for o in raw["ops"]:
        if not isinstance(o, dict) or not isinstance(o.get("op"), str):
            raise SchemaError("op")
        raw_name = o["op"].strip().lower()
        name = OP_ALIASES.get(raw_name, raw_name)
        if name == "set":
            f = o.get("field")
            if f not in FIELDS:
                raise SchemaError("field")
            items = "all" if raw_name == "apply_to_all" else _items(o.get("items", o.get("item")), n_items)
            ops.append({"op": "set", "items": items, "field": f, "value": _value(f, o.get("value"), message, today)})
        elif name == "remove":
            ref = _items(o.get("item", o.get("items")), n_items)
            if ref == "all" or len(ref) != 1:
                raise SchemaError("remove target")
            ops.append({"op": "remove", "item": ref[0]})
        elif name == "add":
            lst = o.get("items")
            if not isinstance(lst, list) or not 1 <= len(lst) <= MAX_ITEMS:
                raise SchemaError("add list")
            ops.append({"op": "add", "items": [_new_item(x, message, today) for x in lst]})
        elif name == "shift":
            d = o.get("days")
            if isinstance(d, bool) or not isinstance(d, int) or d == 0 or abs(d) > MAX_SHIFT:
                raise SchemaError("days")
            ops.append({"op": "shift", "items": _items(o.get("items"), n_items), "days": d})
        elif name == "read":
            if o.get("query") not in READ_QUERIES:
                raise SchemaError("query")
            ops.append({"op": "read", "query": o["query"]})
        elif name in ("confirm", "cancel", "chitchat", "new_request"):
            ops.append({"op": name})
        else:
            raise SchemaError("op name")
    return ops


@dataclass
class Outcome:
    drafts: list[dict]
    changed: bool = False
    cancel: bool = False
    chitchat: bool = False
    new_request: bool = False
    reads: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)     # i18n keys


def _targets(items, n: int) -> list[int]:
    return list(range(n)) if items == "all" else [i - 1 for i in items]


def _set(d: dict, f: str, v, tz) -> str | None:
    s, e = datetime.fromisoformat(d["start"]), datetime.fromisoformat(d["end"])
    if f == "title":
        d["title"] = v
    elif f in ("location", "notes"):
        d[f] = v or ""
    elif f == "reminder_min":
        d["reminder_minutes"] = v
    elif f == "date":
        nd = date.fromisoformat(v)
        ns = datetime.combine(nd, s.timetz().replace(tzinfo=None), tz) if not d["all_day"] else datetime.combine(nd, dtime(0, 0), tz)
        d["start"], d["end"] = ns.isoformat(), (ns + (e - s)).isoformat()
    elif f == "time":
        h, m = map(int, v.split(":"))
        ns = datetime.combine(s.date(), dtime(h, m), tz)
        if d["all_day"]:                             # a time on an all-day draft makes it a timed one (ADR 0015)
            d["all_day"], e, s = False, ns + timedelta(minutes=default_duration(d["title"])), ns
        d["start"], d["end"] = ns.isoformat(), (ns + (e - s)).isoformat()
    elif f == "duration_min":
        if d["all_day"]:
            return "no_change_allday"
        d["end"] = (s + timedelta(minutes=v)).isoformat()
    return None


def apply_ops(drafts: list[dict], ops: list[dict], tz) -> Outcome:
    """Pure: new drafts (deep-copied), plus what else the ops asked for. A model 'confirm' is ignored here: only the
    deterministic `parse_confirm` can confirm, so a mixed 'edit and save' is shown again before anything is saved."""
    out = Outcome(copy.deepcopy(drafts))
    for o in ops:
        n = len(out.drafts)
        name = o["op"]
        if name == "set":
            for i in _targets(o["items"], n):
                if (err := _set(out.drafts[i], o["field"], o["value"], tz)):
                    out.errors.append(err)
            out.changed = True
        elif name == "shift":
            for i in _targets(o["items"], n):
                d = out.drafts[i]
                for k in ("start", "end"):
                    d[k] = (datetime.fromisoformat(d[k]) + timedelta(days=o["days"])).isoformat()
            out.changed = True
        elif name == "remove":
            if n <= 1:
                out.errors.append("dlg_last_item")
            else:
                del out.drafts[o["item"] - 1]
                out.changed = True
        elif name == "add":
            for it in o["items"]:
                if len(out.drafts) >= MAX_ITEMS:
                    break
                if not it["title"] or not it["date"] or not (it["time"] or it["all_day"]):
                    out.errors.append("dlg_item_incomplete")
                    continue
                d = date.fromisoformat(it["date"])
                if it["all_day"]:
                    s = datetime.combine(d, dtime(0, 0), tz)
                    e = s + timedelta(days=1)
                else:
                    h, m = map(int, it["time"].split(":"))
                    s = datetime.combine(d, dtime(h, m), tz)
                    e = s + timedelta(minutes=it["duration_min"] or 60)
                out.drafts.append({"title": it["title"], "start": s.isoformat(), "end": e.isoformat(), "all_day": it["all_day"],
                                   "location": it["location"] or "", "notes": it["notes"] or "",
                                   "reminder_minutes": it["reminder_min"], "recurrence": "", "tag": ""})
                out.changed = True
        elif name == "read":
            out.reads.append(o["query"])
        elif name == "cancel":
            out.cancel = True
        elif name == "chitchat":
            out.chitchat = True
        elif name == "new_request":
            out.new_request = True
    return out


# -- deterministic edit rules (no model): plain slot answers on a shown draft --
_SHIFT = re.compile(r"\b(\d{1,3}|bir|iki|uc|dort|bes)\s*(gun|hafta)\s*(ertele\w*|ileri\w*|sonra\w*|kaydir\w*)")
_NUMW = {"bir": 1, "iki": 2, "uc": 3, "dort": 4, "bes": 5}


def rules_ops(text: str, today: date, n_items: int) -> list[dict] | None:
    """Ops for a message that is ONLY an edit of a shown draft ('saati 16:00 yap', 'yarin olsun', 'hepsini bir gun
    ertele', '1 saat once hatirlat'). None when the message holds other content (a title, a question): the caller
    then keeps the preview and asks."""
    f = " ".join(re.findall(r"[a-z0-9]+", fold(text)))
    target = "all"
    if n_items > 1 and not re.search(r"\bhep\w*|\bbutun\b|\btum\b", f):
        return None                                   # which one? the caller asks
    m = _SHIFT.search(f)
    if m and set(re.sub(_SHIFT, " ", f).split()) <= i18n.EDIT_FILLER:
        n = int(m[1]) if m[1].isdigit() else _NUMW[m[1]]
        return [{"op": "shift", "items": target, "days": n * (7 if m[2] == "hafta" else 1)}]
    if re.fullmatch(r"(hepsini |hepsi )?(bir )?gun (ertele|ileri\w*)", f):
        return [{"op": "shift", "items": target, "days": 1}]
    ops, spans = [], []
    rm = timeparse.find_reminder(text)
    if rm:
        ops.append({"op": "set", "items": target, "field": "reminder_min", "value": rm[0]})
        spans += rm[1]
    rest = timeparse.blank(text, rm[1]) if rm else text
    d = timeparse.find_date(rest, today)
    fz = fuzzytime.find(rest)
    tm = (fz.start, fz.spans) if fz and fz.start else timeparse.find_time(rest)
    if d:
        ops.append({"op": "set", "items": target, "field": "date", "value": d[0].isoformat()})
        spans += d[1]
    if tm:
        ops.append({"op": "set", "items": target, "field": "time", "value": tm[0].strftime("%H:%M")})
        spans += tm[1]
    if fz and fz.start and fz.end:
        ops.append({"op": "set", "items": target, "field": "duration_min",
                    "value": (fz.end.hour - fz.start.hour) * 60 + fz.end.minute - fz.start.minute})
    if fz and fz.duration:
        ops.append({"op": "set", "items": target, "field": "duration_min", "value": fz.duration})
        spans += fz.spans
    if not ops:
        return None
    left = [w for w in re.findall(r"[a-z0-9]+", fold(timeparse.strip_spans(text, spans))) if w not in i18n.EDIT_FILLER]
    return ops if not left else None
