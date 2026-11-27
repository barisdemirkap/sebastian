"""Agent mode tools (ADR 0019): the ONLY things the model can do, bound to the owner chat by construction.

No tool takes a chat id. Every write tool only creates or changes a server-side DRAFT (a pending action with a version,
rendered by code). `commit_draft` runs the stored action only when CODE minted a single-use grant for the exact current
version (`agent.mint_grant`); the model has no tool that mints one. Immediate effects exist only for Sebastian's own store
(lists, tasks, notes), like the dialogue manager. Tool results are data for the model, never shown to the owner as is."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import dialogue, i18n, lists as listmod
from .merge import merge
from .store import Store
from .textutil import clean, title_match

MAX_ITEMS = 20
MAX_NOTES = 60
FIELDS = ("title", "date", "time", "duration_min", "reminder_min", "location", "notes")
CHANGE_KEYS = ("title", "date", "time", "duration_min", "location", "notes", "reminder_min")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
KEY_RE = re.compile(r"[A-Za-z0-9_.-]{1,40}")
TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


class ToolError(Exception):
    """A tool refused: the text is actionable for the model (it becomes an isError result)."""


def _obj(props: dict, required: tuple = ()) -> dict:
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


_S = {"type": "string"}
_DATE = {"type": "string", "description": "ISO date YYYY-MM-DD"}
_TIME = {"type": "string", "description": "24h HH:MM"}
_INT = {"type": "integer"}
_LIST = {"type": "string", "description": "list key; default 'alinacaklar' (the shopping list)"}
_CHANGES = _obj({"title": _S, "date": _DATE, "time": _TIME, "duration_min": _INT, "location": _S, "notes": _S, "reminder_min": _INT})
_OP = _obj({"op": {"type": "string", "enum": ["set", "remove", "shift", "cancel"]},
            "items": {"description": "'all' or a list of 1-based draft item numbers"},
            "item": _INT, "field": {"type": "string", "enum": list(FIELDS)}, "value": {}, "days": _INT}, ("op",))

TOOL_SPECS: dict[str, dict] = {
    "calendar_range": {"description": "Events of the owner's calendar between two dates (inclusive, max 62 days), with ids.",
                       "inputSchema": _obj({"start_date": _DATE, "end_date": _DATE}, ("start_date", "end_date"))},
    "find_events": {"description": "Find upcoming events by words of their title (fuzzy), optionally on one date. Returns ids.",
                    "inputSchema": _obj({"query": _S, "date": _DATE}, ("query",))},
    "propose_add_event": {"description": "Create (or extend) a DRAFT that adds one event. Writes nothing to the calendar. Needs a title, "
                                         "a date and either a time or all_day=true; never guess a missing time. The system shows the owner the preview.",
                          "inputSchema": _obj({"title": _S, "date": _DATE, "time": _TIME, "all_day": {"type": "boolean"},
                                               "duration_min": _INT, "reminder_min": _INT, "location": _S, "notes": _S,
                                               "recurrence": {"type": "string", "enum": ["daily", "weekly", "yearly"]},
                                               "tag": {"type": "string", "enum": ["birthday"]}}, ("title", "date"))},
    "propose_edit_event": {"description": "Create a DRAFT that changes an existing event (id from calendar_range/find_events).",
                           "inputSchema": _obj({"event_id": _S, "changes": _CHANGES, "scope": {"type": "string", "enum": ["this", "all"]}},
                                               ("event_id", "changes"))},
    "propose_delete_event": {"description": "Create a DRAFT that deletes an existing event. Deletions need the owner to say 'evet sil'.",
                             "inputSchema": _obj({"event_id": _S, "scope": {"type": "string", "enum": ["this", "all"]}}, ("event_id",))},
    "show_draft": {"description": "The open draft with its version and the preview text the owner sees (or NO_DRAFT).", "inputSchema": _obj({})},
    "edit_draft": {"description": "Change the open ADD draft: set a field of items, remove an item, shift dates, or cancel the draft "
                                  "(only when the owner clearly drops it). Creates a new version and kills any grant.",
                   "inputSchema": _obj({"draft_version": _INT, "ops": {"type": "array", "items": _OP, "minItems": 1, "maxItems": 12}},
                                       ("draft_version", "ops"))},
    "commit_draft": {"description": "Execute the open draft. Works ONLY when the system line says onay=EVET for this exact version "
                                    "(a grant minted by code); otherwise it refuses and nothing is saved.",
                     "inputSchema": _obj({"draft_version": _INT}, ("draft_version",))},
    "list_get": {"description": "Show a named list (default: the shopping list Alinacaklar).", "inputSchema": _obj({"list": _LIST})},
    "list_add": {"description": "Add items to a list now (immediate, Sebastian's own store).",
                 "inputSchema": _obj({"items": {"type": "array", "items": _S, "minItems": 1, "maxItems": MAX_ITEMS}, "list": _LIST}, ("items",))},
    "list_check": {"description": "Mark list items as bought (by name or number), or all of them (immediate).",
                   "inputSchema": _obj({"items": {"type": "array", "items": _S, "maxItems": MAX_ITEMS}, "all": {"type": "boolean"},
                                        "list": _LIST})},
    "list_remove": {"description": "Create a DRAFT that removes items from a list, or clears the list (clear=true).",
                    "inputSchema": _obj({"items": {"type": "array", "items": _S, "maxItems": MAX_ITEMS}, "clear": {"type": "boolean"},
                                         "list": _LIST})},
    "task_add": {"description": "Add an owner to-do (not a calendar event), optionally for a day (date).",
                 "inputSchema": _obj({"title": _S, "date": _DATE}, ("title",))},
    "task_set_due": {"description": "Move an EXISTING to-do to another day ('X cumartesiye kalsin', 'X yarina'). Changes only the to-do's day "
                                    "(immediate, Sebastian's own store); never touches the calendar. Use find_events/propose_edit_event for events.",
                     "inputSchema": _obj({"title": _S, "date": _DATE}, ("title", "date"))},
    "task_list": {"description": "Open to-dos.", "inputSchema": _obj({})},
    "task_done": {"description": "Mark a to-do done (or remove=true to drop it). Mentions matching events; it never touches the calendar.",
                  "inputSchema": _obj({"title": _S, "remove": {"type": "boolean"}}, ("title",))},
    "note_set": {"description": "Remember a fact or preference of the owner across sessions (key: short slug, e.g. cop_sure, "
                                "default_reminder_min, routine_morning). An empty value deletes the note.",
                 "inputSchema": _obj({"key": _S, "value": _S}, ("key", "value"))},
    "note_get": {"description": "Read one stored note, or all notes when no key is given.", "inputSchema": _obj({"key": _S})},
    "recent_messages": {"description": "The last chat messages of the owner and Sebastian (max 20), oldest first.",
                        "inputSchema": _obj({"n": {"type": "integer", "minimum": 1, "maximum": 20}})},
}
TOOL_NAMES = tuple(TOOL_SPECS)
WRITE_TOOLS = ("propose_add_event", "propose_edit_event", "propose_delete_event", "edit_draft", "list_remove")


@dataclass
class Effects:
    """What the tools did during one turn (the orchestrator composes the reply from it)."""
    calls: list[str] = field(default_factory=list)
    drafted: int | None = None                  # pending id of the draft created or changed this turn
    committed: list[str] = field(default_factory=list)   # code-rendered receipts of executed drafts
    immediate: bool = False                     # a list, task or note was written
    cancelled: bool = False


class AgentTools:
    def __init__(self, assistant):
        self.a = assistant
        self.effects = Effects()

    # ------------------------------------------------------------------ dispatch
    def call(self, name: str, args: dict | None) -> tuple[str, bool]:
        """(text, is_error). Never raises: any failure becomes an error result the model can read."""
        args = args if isinstance(args, dict) else {}
        fn = getattr(self, "t_" + name, None) if name in TOOL_SPECS else None
        if fn is None:
            return f"UNKNOWN_TOOL: {name}", True
        if (off := self.a.plugins.blocked("tools", name)):
            return f"PLUGIN_DISABLED: the {off} plugin is switched off in the config; tell the owner it is off and do nothing.", True
        self.effects.calls.append(name)
        try:
            return fn(args), False
        except ToolError as exc:
            return str(exc), True
        except (KeyError, ValueError, TypeError) as exc:
            return f"BAD_ARGUMENTS: {type(exc).__name__}: {exc}", True
        except Exception as exc:  # noqa: BLE001
            self.a.store.audit("error", {"type": type(exc).__name__, "tool": name}, self.a.clock(), self.a.owner)
            return "TOOL_FAILED: internal error; tell the owner you could not do it right now.", True

    # ------------------------------------------------------------------ helpers
    @property
    def now(self) -> datetime:
        return self.a.clock()

    def _open(self):
        """The open, unexpired draft (a pending action) of the owner, or None. An expired one is closed."""
        row = self.a.store.open_pending(self.a.owner)
        if row is None:
            return None
        if Store.pending_state(row, self.now) != "ok":
            self.a.store.close_pending(row["id"], "expired")
            return None
        return row

    @staticmethod
    def _text(args: dict, key: str, limit: int, required: bool = False) -> str | None:
        v = args.get(key)
        if v is None or v == "":
            if required:
                raise ToolError(f"BAD_ARGUMENTS: {key} is required")
            return None
        if not isinstance(v, str):
            raise ToolError(f"BAD_ARGUMENTS: {key} must be a string")
        return clean(v, limit) or None

    @staticmethod
    def _date(v, key: str = "date") -> date:
        try:
            return date.fromisoformat(str(v))
        except ValueError as exc:
            raise ToolError(f"BAD_ARGUMENTS: {key} must be an ISO date YYYY-MM-DD") from exc

    @staticmethod
    def _int(v, key: str, lo: int, hi: int) -> int:
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ToolError(f"BAD_ARGUMENTS: {key} must be an integer {lo}..{hi}")
        return v

    def _line(self, e) -> str:
        s = e.start.astimezone(self.a.tz)
        when = "all-day" if e.all_day else f"{s:%H:%M}-{e.end.astimezone(self.a.tz):%H:%M}"
        loc = f" @ {e.location}" if e.location else ""
        series = " (recurring)" if e.series_id else ""
        return f"[{e.event_id}] {s:%Y-%m-%d} {WEEKDAYS[s.weekday()]} {when} {e.title}{loc}{series}"

    def _drafted(self, text: str) -> str:
        pid = self.a.shown
        self.effects.drafted = pid
        return f"DRAFT v{pid} ready. {text} The system shows the owner the exact preview after your reply; do not repeat it."

    def _no_other_draft(self, kind: str | None = None):
        row = self._open()
        if row is not None and row["intent"] != kind:
            raise ToolError("DRAFT_OPEN: another draft is open (show_draft). Finish it, or cancel it with edit_draft op cancel "
                            "if the owner clearly drops it, before starting something new.")
        return row

    # ------------------------------------------------------------------ calendar reads
    def t_calendar_range(self, args: dict) -> str:
        d0, d1 = self._date(args.get("start_date"), "start_date"), self._date(args.get("end_date"), "end_date")
        if d1 < d0 or (d1 - d0).days > 61:
            raise ToolError("BAD_ARGUMENTS: end_date must be on or after start_date, at most 62 days apart")
        tz = self.a.tz
        start = datetime.combine(d0, datetime.min.time(), tz)
        try:
            evs = merge(self.a.hub.events(start, start + timedelta(days=(d1 - d0).days + 1)))
        except Exception as exc:  # noqa: BLE001
            raise ToolError("CALENDAR_UNAVAILABLE: the calendar cannot be reached right now") from exc
        rows = [self._line(e) for e in evs if e.event_id][:60]
        return "\n".join(rows) if rows else "NO_EVENTS"

    def t_find_events(self, args: dict) -> str:
        q = self._text(args, "query", 80, True)
        on = self._date(args["date"]).isoformat() if args.get("date") else None
        try:
            found = self.a.find_events(q, on, self.now)
        except Exception as exc:  # noqa: BLE001
            raise ToolError("CALENDAR_UNAVAILABLE: the calendar cannot be reached right now") from exc
        return "\n".join(self._line(e) for e in found[:10]) if found else "NO_MATCH"

    # ------------------------------------------------------------------ drafts
    def t_propose_add_event(self, args: dict) -> str:
        a, now = self.a, self.now
        title = self._text(args, "title", 80, True)
        d = self._date(args.get("date"))
        if d < now.date():
            raise ToolError("BAD_ARGUMENTS: that date is in the past; ask the owner which day they mean")
        all_day, time_ = bool(args.get("all_day")), args.get("time")
        if time_ is not None and not (isinstance(time_, str) and TIME_RE.fullmatch(time_)):
            raise ToolError("BAD_ARGUMENTS: time must be HH:MM (24h)")
        if not all_day and not time_:
            raise ToolError("MISSING_TIME: ask the owner for the time in ONE short question (or all_day=true only if they said it "
                            "is an all-day chore). Never guess.")
        it = {"title": title, "date": d.isoformat(), "time": None if all_day else time_, "all_day": all_day,
              "location": self._text(args, "location", 80), "notes": self._text(args, "notes", 300),
              "recurrence": args.get("recurrence") if args.get("recurrence") in ("daily", "weekly", "yearly") else None,
              "tag": "birthday" if args.get("tag") == "birthday" else None}
        if args.get("duration_min") is not None:
            it["duration_min"] = self._int(args["duration_min"], "duration_min", 1, 1440)
        if args.get("reminder_min") is not None:
            it["reminder_min"] = self._int(args["reminder_min"], "reminder_min", 0, 20160)
        draft = a._build_draft(it)
        if (dup := a._duplicate(draft, now)) is not None:
            raise ToolError("ALREADY_EXISTS: " + a._already_text(dup) + " Tell the owner; propose nothing.")
        row = self._no_other_draft("add_event")
        from .assistant import draft_to_dict
        drafts = (json.loads(row["args_json"])["drafts"] if row else []) + [draft_to_dict(draft)]
        if len(drafts) > 30:
            raise ToolError("TOO_MANY: at most 30 events per draft")
        a.shown = None
        a._propose("add_event", {"drafts": drafts}, [], now)
        return self._drafted(f"{len(drafts)} event(s) in the draft.")

    def _target_event(self, args: dict):
        eid = self._text(args, "event_id", 200, True)
        ev = self.a._event_by_id(eid)
        if ev is None:
            raise ToolError("NOT_FOUND: no event with that id (use find_events or calendar_range)")
        return ev

    def t_propose_edit_event(self, args: dict) -> str:
        ch = args.get("changes")
        if not isinstance(ch, dict) or not ch or any(k not in CHANGE_KEYS for k in ch):
            raise ToolError(f"BAD_ARGUMENTS: changes must be an object with some of {list(CHANGE_KEYS)}")
        clean_ch: dict = {}
        for k, v in ch.items():
            if k == "date":
                clean_ch[k] = self._date(v, "changes.date").isoformat()
            elif k == "time":
                if not (isinstance(v, str) and TIME_RE.fullmatch(v)):
                    raise ToolError("BAD_ARGUMENTS: changes.time must be HH:MM")
                clean_ch[k] = v
            elif k == "duration_min":
                clean_ch[k] = self._int(v, k, 1, 1440)
            elif k == "reminder_min":
                clean_ch[k] = self._int(v, k, 0, 20160)
            else:
                clean_ch[k] = self._text({k: v}, k, 80 if k != "notes" else 300, True)
        self._no_other_draft(None)
        ev = self._target_event(args)
        scope = args.get("scope") if args.get("scope") in ("this", "all") else "this"
        self.a.shown = None
        out = self.a._propose_target({"intent": "edit_event", "match": ev.title, "date": None, "scope": scope, "changes": clean_ch},
                                     ev, self.now)
        if self.a.shown is None:
            raise ToolError("NO_DRAFT: " + out[0])
        return self._drafted("Edit of an existing event.")

    def t_propose_delete_event(self, args: dict) -> str:
        self._no_other_draft(None)
        ev = self._target_event(args)
        scope = args.get("scope") if args.get("scope") in ("this", "all") else "this"
        self.a.shown = None
        self.a._propose_target({"intent": "delete_event", "match": ev.title, "date": None, "scope": scope}, ev, self.now)
        return self._drafted("Deletion; the owner must answer 'evet sil'.")

    def t_show_draft(self, args: dict) -> str:
        row = self._open()
        if row is None:
            return "NO_DRAFT"
        return f"DRAFT v{row['id']} kind={row['intent']}\n" + self.a.render_pending(row)

    def _ops(self, raw, n: int) -> list[dict]:
        if not isinstance(raw, list) or not 1 <= len(raw) <= 12:
            raise ToolError("BAD_ARGUMENTS: ops must be 1..12 operations")
        out = []
        for o in raw:
            if not isinstance(o, dict) or o.get("op") not in ("set", "remove", "shift", "cancel"):
                raise ToolError("BAD_ARGUMENTS: op must be one of set, remove, shift, cancel")
            name = o["op"]
            if name == "cancel":
                out.append({"op": "cancel"})
                continue
            try:
                items = dialogue._items(o.get("items", o.get("item")), n)
            except Exception as exc:  # noqa: BLE001
                raise ToolError(f"BAD_ARGUMENTS: item numbers must be 1..{n} or 'all'") from exc
            if name == "remove":
                if items == "all" or len(items) != 1:
                    raise ToolError("BAD_ARGUMENTS: remove takes exactly one item")
                out.append({"op": "remove", "item": items[0]})
            elif name == "shift":
                days = self._int(o.get("days"), "days", -366, 366)
                if days == 0:
                    raise ToolError("BAD_ARGUMENTS: days must not be 0")
                out.append({"op": "shift", "items": items, "days": days})
            else:
                f, v = o.get("field"), o.get("value")
                if f not in FIELDS:
                    raise ToolError(f"BAD_ARGUMENTS: field must be one of {list(FIELDS)}")
                if f == "date":
                    v = self._date(v, "value").isoformat()
                elif f == "time":
                    if not (isinstance(v, str) and TIME_RE.fullmatch(v)):
                        raise ToolError("BAD_ARGUMENTS: time must be HH:MM")
                elif f == "duration_min":
                    v = self._int(v, f, 1, 1440)
                elif f == "reminder_min":
                    v = self._int(v, f, 0, 20160)
                else:
                    v = self._text({f: v}, f, 80 if f != "notes" else 300, f == "title")
                out.append({"op": "set", "items": items, "field": f, "value": v})
        return out

    def t_edit_draft(self, args: dict) -> str:
        a, now = self.a, self.now
        row = self._open()
        if row is None:
            raise ToolError("NO_DRAFT: nothing to edit")
        v = self._int(args.get("draft_version"), "draft_version", 0, 10**9)
        if v != row["id"]:
            raise ToolError(f"VERSION_MISMATCH: current version is {row['id']}; call show_draft")
        is_add = row["intent"] == "add_event"
        drafts = json.loads(row["args_json"]).get("drafts", []) if is_add else []
        ops = self._ops(args.get("ops"), len(drafts))
        if not is_add and any(o["op"] != "cancel" for o in ops):
            raise ToolError("NOT_EDITABLE: only add drafts can be edited; cancel it or create a new draft")
        out = dialogue.apply_ops(drafts, ops, a.tz)
        if out.cancel:
            a.store.close_pending(row["id"], "cancelled")
            self.effects.cancelled = True
            return "DRAFT_CANCELLED"
        if out.errors:
            raise ToolError("REFUSED: " + " ".join(i18n.t(k) for k in dict.fromkeys(out.errors)))
        if not out.changed or out.drafts == drafts:
            raise ToolError("NO_CHANGE: the draft already looks like that")
        from .assistant import dict_to_draft
        ttl = datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"])
        a.shown = None
        a._propose("add_event", {"drafts": out.drafts}, [dict_to_draft(x) for x in out.drafts], now, ttl)
        return self._drafted("Edited; earlier confirmation no longer counts.")

    def t_commit_draft(self, args: dict) -> str:
        a, now = self.a, self.now
        row = self._open()
        if row is None:
            raise ToolError("NO_DRAFT: nothing to save")
        v = self._int(args.get("draft_version"), "draft_version", 0, 10**9)
        if v != row["id"]:
            raise ToolError(f"VERSION_MISMATCH: current version is {row['id']}; call show_draft")
        if not a.store.grant_consume(a.owner, row["id"], row["args_hash"]):
            raise ToolError("NOT_CONFIRMED: the owner has not confirmed this exact version of the draft. Do NOT save and do not say "
                            "it is saved. The system will show the preview; wait for a plain yes.")
        a.shown = None
        replies = a._confirm(row, "evet sil" if row["intent"] == "delete_event" else "evet", now)
        self.effects.committed.extend(replies)
        return "COMMITTED: " + " ".join(replies)

    # ------------------------------------------------------------------ lists (Sebastian's own store)
    def _list_key(self, args: dict) -> str:
        key = args.get("list") or i18n.DEFAULT_LIST
        if key not in i18n.LISTS:
            raise ToolError(f"BAD_ARGUMENTS: list must be one of {list(i18n.LISTS)}")
        return key

    def _items(self, args: dict) -> list[str]:
        v = args.get("items")
        if not isinstance(v, list) or any(not isinstance(x, str) for x in v) or len(v) > MAX_ITEMS:
            raise ToolError("BAD_ARGUMENTS: items must be a list of strings")
        return [listmod._cap(clean(x, 80)) for x in v if clean(x, 80)]

    def t_list_get(self, args: dict) -> str:
        return self.a._list_view(self._list_key(args), self.now)

    def t_list_add(self, args: dict) -> str:
        key, items = self._list_key(args), self._items(args)
        if not items:
            raise ToolError("BAD_ARGUMENTS: no items")
        added, dups = self.a._list_add(key, items, self.now)
        self.effects.immediate = True
        return f"ADDED: {', '.join(added) or '-'}; ALREADY_THERE: {', '.join(dups) or '-'}\n" + self.a._list_view(key, self.now)

    def t_list_check(self, args: dict) -> str:
        key, now, a = self._list_key(args), self.now, self.a
        if args.get("all"):
            hit = a.store.list_items(a.owner, key)
        else:
            hit, seen = [], set()
            for m in self._items(args):
                for r in a._pick_items(key, m):
                    if r["id"] not in seen:
                        seen.add(r["id"])
                        hit.append(r)
        if not hit:
            raise ToolError("NOT_FOUND: no open list item matches")
        a.store.close_list_items([r["id"] for r in hit], "done", now)
        self.effects.immediate = True
        return f"CHECKED: {', '.join(r['text'] for r in hit)}\n" + a._list_view(key, now)

    def t_list_remove(self, args: dict) -> str:
        key, a, now = self._list_key(args), self.a, self.now
        self._no_other_draft(None)
        a.shown = None
        if args.get("clear"):
            out = a._list_clear_preview(key, now)
            if a.shown is None:
                raise ToolError("NO_CHANGE: " + out[0])
            return self._drafted("Clearing the list.")
        hit, seen = [], set()
        for m in self._items(args):
            for r in a._pick_items(key, m):
                if r["id"] not in seen:
                    seen.add(r["id"])
                    hit.append(r)
        if not hit:
            raise ToolError("NOT_FOUND: no open list item matches")
        a._propose("list_remove", {"list": key, "ids": [r["id"] for r in hit], "items": [r["text"] for r in hit]}, [], now)
        return self._drafted("Removing list items.")

    # ------------------------------------------------------------------ tasks
    def t_task_add(self, args: dict) -> str:
        title = self._text(args, "title", 80, True)
        row = self.a._find_task(title)
        if row and row["title"].casefold() == title.casefold():
            return f"ALREADY_THERE: {row['title']}"
        due = self._date(args["date"], "date").isoformat() if args.get("date") else None
        self.a.store.add_task(self.a.owner, title[:1].upper() + title[1:], self.now, due)
        self.effects.immediate = True
        return f"TASK_ADDED: {title}" + (f" (due {due})" if due else "")

    def t_task_set_due(self, args: dict) -> str:
        title = self._text(args, "title", 80, True)
        row = self.a._find_task(title)
        if row is None:
            raise ToolError("NOT_FOUND: no open task with that name (it may be an event: use find_events and propose_edit_event)")
        day = self._date(args.get("date"), "date")
        self.a.store.set_task_due(row["id"], day.isoformat())
        self.effects.immediate = True
        return f"TASK_MOVED: {row['title']} -> {day.isoformat()}"

    def t_task_list(self, args: dict) -> str:
        rows = self.a.store.open_tasks(self.a.owner)
        return "\n".join(f"{i}. {r['title']}" for i, r in enumerate(rows, 1)) if rows else "NO_OPEN_TASKS"

    def t_task_done(self, args: dict) -> str:
        title = self._text(args, "title", 80, True)
        row = self.a._find_task(title)
        if row is None:
            raise ToolError("NOT_FOUND: no open task with that name")
        self.a.store.close_task(row["id"], "removed" if args.get("remove") else "done", self.now)
        self.effects.immediate = True
        evs = self.a._events_soon(title, self.now)
        extra = ("\nMATCHING_EVENTS (offer deleting one with propose_delete_event only if the owner wants it):\n"
                 + "\n".join(self._line(e) for e in evs[:5])) if evs else ""
        return f"TASK_{'REMOVED' if args.get('remove') else 'DONE'}: {row['title']}{extra}"

    # ------------------------------------------------------------------ notes and history
    def t_note_set(self, args: dict) -> str:
        key, value = args.get("key"), args.get("value")
        if not isinstance(key, str) or not KEY_RE.fullmatch(key) or not isinstance(value, str):
            raise ToolError("BAD_ARGUMENTS: key must be a short slug (letters, digits, _ . -) and value a string")
        st = self.a.store
        if not value.strip():
            st.note_delete(self.a.owner, key)
            return f"NOTE_DELETED: {key}"
        if len(st.notes(self.a.owner)) >= MAX_NOTES and not any(r["key"] == key for r in st.notes(self.a.owner)):
            raise ToolError("TOO_MANY_NOTES: delete an old note first (note_set with an empty value)")
        st.note_set(self.a.owner, key, clean(value, 300), self.now)
        self.effects.immediate = True
        return f"NOTE_SAVED: {key}"

    def t_note_get(self, args: dict) -> str:
        rows = self.a.store.notes(self.a.owner)
        if args.get("key"):
            hit = [r for r in rows if r["key"] == args["key"]]
            return hit[0]["value"] if hit else "NO_NOTE"
        return "\n".join(f"{r['key']}: {r['value']}" for r in rows) or "NO_NOTES"

    def t_recent_messages(self, args: dict) -> str:
        n = self._int(args.get("n", 10), "n", 1, 20)
        rows = self.a.store.recent_messages(self.a.owner, n)
        return "\n".join(f"{'OWNER' if r['direction'] == 'in' else 'SEBASTIAN'} {r['at'][11:16]}: {clean(r['text'], 200)}" for r in rows) \
            or "NO_MESSAGES"
