"""Replays the incident fixtures against AGENT MODE (ADR 0019, 0020).

The fixtures describe what the owner says and what state must result. A scripted fake model needs tool calls, so they are
DERIVED once per fixture: the fixture is run against the dialogue manager and, for every handled message, the state
change (new or edited draft, executed draft, list and task changes) is turned into the tool calls a good model would
make. Replaying those directives through the real agent stack (grants, tools, composition, Bot loop, buttons) must give
the same state and keep every invariant I1-I10. Text-level expectations are manager wording and are not asserted here;
state expectations are. Also used by scripts/agent_eval.py (the real model gets only the owner's words)."""
from __future__ import annotations
import copy
import json
from datetime import datetime

import invariants as inv
from sebastian.agent import RunResult
from sebastian_adapter import make_adapter

TEXT_KEYS = ("contains", "not_contains", "replies_max")


def agent_script(script: dict) -> dict:
    """The same conversation without the manager-wording expectations (state expectations stay)."""
    out = copy.deepcopy(script)
    for st in out["steps"]:
        for k in TEXT_KEYS:
            st.pop(k, None)
    return out


# ---------------------------------------------------------------------------------------------------- snapshots
def snapshot(adapter) -> dict:
    st, owner = adapter.store, adapter.owner
    row = st.open_pending(owner)
    lists = {}
    for key in ("alinacaklar",):
        lists[key] = {s: [r["text"] for r in st.list_items(owner, key, s)] for s in ("open", "done")}
    return {"pending": dict(row) if row else None, "events": {e.event_id: e for e in adapter.hub._events},
            "lists": lists, "tasks": {r["id"]: r["title"] for r in st.open_tasks(owner)},
            "task_all": {r["id"]: (r["title"], r["status"]) for r in st.c.execute("SELECT * FROM tasks")},
            "task_due": {r["id"]: r["due"] for r in st.c.execute("SELECT * FROM tasks")}}


def _draft_args(d: dict) -> dict:
    s, e = datetime.fromisoformat(d["start"]), datetime.fromisoformat(d["end"])
    a = {"title": d["title"], "date": s.date().isoformat(), "all_day": bool(d["all_day"])}
    if not d["all_day"]:
        a["time"], a["duration_min"] = f"{s:%H:%M}", int((e - s).total_seconds() // 60)
    for k_, f_ in (("reminder_min", "reminder_minutes"), ("location", "location"), ("notes", "notes"),
                   ("recurrence", "recurrence"), ("tag", "tag")):
        if d.get(f_) not in (None, ""):
            a[k_] = d[f_]
    return a


def _draft_ops(before: list[dict], after: list[dict]) -> list[dict] | None:
    """edit_draft ops that turn `before` into `after`, or None when the shapes differ too much (then: cancel and re-propose)."""
    if len(before) != len(after):
        return None
    ops = []
    for i, (b, a) in enumerate(zip(before, after), 1):
        bs, as_ = datetime.fromisoformat(b["start"]), datetime.fromisoformat(a["start"])
        be, ae = datetime.fromisoformat(b["end"]), datetime.fromisoformat(a["end"])
        if b["all_day"] != a["all_day"] or b.get("recurrence") != a.get("recurrence") or b.get("tag") != a.get("tag"):
            return None
        if b["title"] != a["title"]:
            ops.append({"op": "set", "items": [i], "field": "title", "value": a["title"]})
        if as_.date() != bs.date():
            ops.append({"op": "set", "items": [i], "field": "date", "value": as_.date().isoformat()})
        if not a["all_day"] and as_.time() != bs.time():
            ops.append({"op": "set", "items": [i], "field": "time", "value": f"{as_:%H:%M}"})
        if not a["all_day"] and (ae - as_) != (be - bs):
            ops.append({"op": "set", "items": [i], "field": "duration_min", "value": int((ae - as_).total_seconds() // 60)})
        if b.get("reminder_minutes") != a.get("reminder_minutes") and a.get("reminder_minutes") is not None:
            ops.append({"op": "set", "items": [i], "field": "reminder_min", "value": a["reminder_minutes"]})
        for f in ("location", "notes"):
            if (b.get(f) or "") != (a.get(f) or ""):
                ops.append({"op": "set", "items": [i], "field": f, "value": a.get(f) or ""})
    return ops or None


def _create_calls(row: dict) -> list[tuple]:
    args = json.loads(row["args_json"])
    if row["intent"] == "add_event":
        return [("propose_add_event", _draft_args(d)) for d in args["drafts"]]
    if row["intent"] == "edit_event":
        ch, out = args["changes"], {}
        for k_, f_ in (("title", "title"), ("location", "location"), ("notes", "notes"), ("reminder_min", "reminder_minutes")):
            if f_ in ch:
                out[k_] = ch[f_]
        if "start" in ch:
            s, e = datetime.fromisoformat(ch["start"]), datetime.fromisoformat(ch["end"])
            out.update({"date": s.date().isoformat(), "time": f"{s:%H:%M}", "duration_min": int((e - s).total_seconds() // 60)})
        return [("propose_edit_event", {"event_id": args["event_id"], "changes": out, "scope": args.get("scope", "this")})]
    if row["intent"] == "delete_event":
        return [("propose_delete_event", {"event_id": args["event_id"], "scope": args.get("scope", "this")})]
    if row["intent"] == "list_remove":
        return [("list_remove", {"items": args["items"], "list": args["list"]})]
    if row["intent"] == "list_clear":
        return [("list_remove", {"clear": True, "list": args["list"]})]
    return []


def directive(before: dict, after: dict, store_status, replies: list[str]) -> dict:
    """The tool calls and the words of a good model for one handled message, from the manager's state change."""
    calls: list[tuple] = []
    b, a = before["pending"], after["pending"]
    for tid, (title, status) in after["task_all"].items():
        if tid not in before["task_all"]:
            if status == "open":
                calls.append(("task_add", {"title": title, **({"date": after["task_due"][tid]} if after["task_due"].get(tid) else {})}))
        elif before["task_all"][tid][1] == "open" and status != "open":
            calls.append(("task_done", {"title": title, "remove": status == "removed"}))
        elif status == "open" and after["task_due"].get(tid) and after["task_due"][tid] != before["task_due"].get(tid):
            calls.append(("task_set_due", {"title": title, "date": after["task_due"][tid]}))
    for key, now_ in after["lists"].items():
        was = before["lists"][key]
        new = [x for x in now_["open"] if x not in was["open"] and x not in was["done"]]
        checked = [x for x in now_["done"] if x not in was["done"]]
        if new:
            calls.append(("list_add", {"items": new, "list": key}))
        if checked:
            calls.append(("list_check", {"items": checked, "list": key}))
    executed = b is not None and store_status(b["id"]) == "done"
    if executed:
        calls.append(("commit_draft", {"draft_version": "@draft"}))
        if a is not None and a["id"] != b["id"]:
            calls += _create_calls(a)               # a queued clause's preview opens right after the receipt
    elif a is not None and (b is None or b["id"] != a["id"]):
        if b is not None:
            ops = None
            if b["intent"] == "add_event" and a["intent"] == "add_event":
                ops = _draft_ops(json.loads(b["args_json"])["drafts"], json.loads(a["args_json"])["drafts"])
            if ops:
                calls.append(("edit_draft", {"draft_version": "@draft", "ops": ops}))
                return {"calls": calls, "text": "Hemen, efendim."}
            calls.append(("edit_draft", {"draft_version": "@draft", "ops": [{"op": "cancel"}]}))
        calls += _create_calls(a)
    elif a is None and b is not None and store_status(b["id"]) in ("cancelled", "superseded"):
        calls.append(("edit_draft", {"draft_version": "@draft", "ops": [{"op": "cancel"}]}))
    state_change = bool(calls)
    text = "Hemen, efendim." if state_change else "\n".join(replies)
    return {"calls": calls, "text": text}


def derive(script: dict) -> dict[int, dict]:
    """Run the fixture against the manager; {handle ordinal: directive}. Every handled owner message has one."""
    adapter = make_adapter({**script.get("setup", {})})
    asst, out = adapter.assistant, {}
    real = asst.handle

    def wrapped(chat_id, text, voice=False):
        before = snapshot(adapter)
        replies = real(chat_id, text, voice)
        if chat_id == adapter.owner:
            out[asst.turn_no] = directive(before, snapshot(adapter), lambda pid: adapter.store.get_pending(pid)["status"], replies)
        return replies
    asst.handle = wrapped
    inv.run_script(lambda s: adapter, script)
    return out


class ReplayRunner:
    """The scripted fake model: handle ordinal -> directive. The string "@draft" is the current open draft version."""
    needs_bridge = False

    def __init__(self, directives: dict[int, dict]):
        self.directives, self.requests, self.results = directives, [], []

    def run(self, req):
        self.requests.append(req)
        a = req.tools.a
        d = self.directives.get(a.turn_no, {"calls": [], "text": ""})
        for name, args in d["calls"]:
            row = a.store.open_pending(a.owner)
            args = {k: (row["id"] if v == "@draft" and row else v) for k, v in args.items()}
            self.results.append((name, *req.tools.call(name, args)))
        return RunResult(text=d["text"], tool_calls=[c[0] for c in d["calls"]])


def run_in_agent_mode(script: dict, runner=None):
    """The fixture (state expectations only) against agent mode with the replay model; returns (conversation, runner)."""
    runner = runner or ReplayRunner(derive(script))
    ascript = agent_script(script)
    ascript["setup"] = {**ascript.get("setup", {}), "agent_runner": runner}
    conv = inv.run_script(make_adapter, ascript)
    return conv, runner
