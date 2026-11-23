"""Shared helpers for the agent-mode tests: a scripted fake model (no process, no network) and a wiring factory."""
from __future__ import annotations
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sebastian import nl as nlmod
from sebastian.agent import AgentMode, RunResult
from sebastian.assistant import Assistant
from sebastian.models import Event
from sebastian.providers import FixtureProvider
from sebastian.store import Store

IST = ZoneInfo("Europe/Istanbul")
OWNER = 4242                                   # fictional
NOW = datetime(2026, 11, 19, 10, 0, tzinfo=IST)  # Thursday


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def ev(title, day, h, m=0, dur=60, eid=None, **kw):
    s = datetime(2026, 11, day, h, m, tzinfo=IST)
    return Event(title, s, s + timedelta(minutes=dur), "personal", uid=eid or title, event_id=eid or title, **kw)


class ScriptedRunner:
    """A fake model. Each turn takes the next script: {"calls": [(tool, args), ...], "text": str} or a callable(request) ->
    RunResult. In an args dict the string "@draft" is replaced by the current open draft version. Requests are recorded."""
    needs_bridge = False

    def __init__(self, scripts=()):
        self.scripts, self.requests, self.results = list(scripts), [], []

    def push(self, text="", calls=()):
        self.scripts.append({"calls": list(calls), "text": text})

    def run(self, req):
        self.requests.append(req)
        if not self.scripts:
            return RunResult(text="")
        sc = self.scripts.pop(0)
        if callable(sc):
            return sc(req)
        a = req.tools.a
        for name, args in sc.get("calls", []):
            row = a.store.open_pending(a.owner)
            args = {k: (row["id"] if v == "@draft" and row else v) for k, v in args.items()}
            self.results.append(req.tools.call(name, args))
        return RunResult(text=sc.get("text", ""), tool_calls=[c[0] for c in sc.get("calls", [])])


def make(events=(), scripts=(), **agent_kw):
    hub = FixtureProvider(list(events))
    clock = Clock()
    st = Store()
    a = Assistant(hub, st, nlmod.NLService("off"), IST, OWNER, clock)
    runner = ScriptedRunner(scripts)
    a.agent = AgentMode(a, runner, **agent_kw)
    return a, hub, st, clock, runner


def say(a, text, log=True):
    """One owner message through the assistant; like the bot loop it logs both directions (the gate reads the log)."""
    now = a.clock()
    if log:
        a.store.log(OWNER, text, "in", now)
    out = a.handle(OWNER, text)
    if log:
        for r in out:
            a.store.log(OWNER, r, "out", now)
    return out
