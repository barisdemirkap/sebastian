"""The ONLY Sebastian-specific file of the conformance suite: wires the real Assistant + Bot loop + a fake Telegram
transport + a fake model provider + a fake calendar to the bot-agnostic adapter contract in invariants.py."""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sebastian import dialogue, nl as nlmod
from sebastian.assistant import Assistant
from sebastian.bot import Bot
from sebastian.models import Event, EventDraft
from sebastian.providers import FixtureProvider
from sebastian.store import Store
from sebastian.telegram import FakeTelegram

from invariants import Msg

IST = ZoneInfo("Europe/Istanbul")
OWNER = 4242                                  # fictional chat id
NOW = datetime(2026, 11, 18, 10, 0, tzinfo=IST)


class RawTelegram(FakeTelegram):
    """Ignores the offset, so a redelivered update really reaches the bot (its own dedupe must catch it)."""

    def get_updates(self, offset, timeout=0):
        out, self.inbox = self.inbox, []
        return out


class ClaudeLike:
    """A tiny stand-in for the model: `set time` edits while a preview is open, chit-chat otherwise, unclear for the rest."""
    name, model = "fake", "fake-1"

    def __init__(self, intent: dict | None = None):
        self.calls = []
        self.intent = intent             # what the model answers to a fresh request (setup "model_intent")

    def parse(self, message, context):
        self.calls.append(message)
        if context.get("dialogue"):
            m = re.search(r"\b(\d{1,2}):(\d{2})\b", message)
            if m and "hepsi" in message.lower():
                return {"ops": [{"op": "apply_to_all", "field": "time", "value": f"{int(m[1]):02d}:{m[2]}"}]}
            return {"ops": [{"op": "chitchat"}]}
        return dict(self.intent) if self.intent else {"intent": "unclear", "question": None}


def _drafts(n: int, day: int = 19) -> list[EventDraft]:
    out = []
    for i in range(n):
        s = datetime(2026, 11, day + i, 10 + i % 5, tzinfo=IST)
        out.append(EventDraft(f"Demo toplantı {i + 1}", s, s + timedelta(hours=1)))
    return out


class SebastianAdapter:
    owner = OWNER

    def __init__(self, setup: dict):
        provider = setup.get("provider", "rules")
        self.hub = FixtureProvider([])
        self.store = Store()
        self.now = datetime.fromisoformat(setup["now"]).replace(tzinfo=IST) if setup.get("now") else NOW
        nl = nlmod.NLService("off")
        if provider == "claude_like":
            nl = nlmod.NLService("claude", {"claude": ClaudeLike(setup.get("model_intent")), "qwen": _Down()}, consent=True)
        elif provider == "flaky":          # a model that can be switched off and on (ADR 0021)
            self.flaky = Flaky(setup.get("model_intents", {}), setup.get("model_up", True))
            nl = nlmod.NLService("claude", {"claude": self.flaky, "qwen": _Down()}, consent=True)
        elif provider == "fail":
            nl = nlmod.NLService("claude", {"claude": _Down(), "qwen": _Down()}, consent=True)
        self.assistant = Assistant(self.hub, self.store, nl, IST, OWNER, lambda: self.now)
        if setup.get("agent_runner") is not None:     # agent mode (ADR 0019): the model edge is injected (scripted fake or real)
            from sebastian.agent import AgentMode
            self.assistant.agent = AgentMode(self.assistant, setup["agent_runner"], **setup.get("agent_kwargs", {}))
        self.api = RawTelegram()
        self.bot = Bot(self.api, self.assistant, self.store, OWNER, lambda: self.now, sleep=lambda s: None, settle_wait=0)
        for item in setup.get("seed", []):       # [title, hour] | [title, hour, "YYYY-MM-DD"] | {"title","start","end","reminder"}
            if isinstance(item, dict):
                s0 = datetime.fromisoformat(item["start"]).replace(tzinfo=IST)
                e0 = datetime.fromisoformat(item["end"]).replace(tzinfo=IST)
                self.hub.create_event(EventDraft(item["title"], s0, e0, reminder_minutes=item.get("reminder")))
                continue
            title, hour, *day = item
            s = datetime.fromisoformat(day[0] if day else "2026-11-19").replace(hour=hour, tzinfo=IST)
            self.hub.create_event(EventDraft(title, s, s + timedelta(hours=1)))
        for t in setup.get("tasks", []):             # open to-dos (Sebastian's own list): "title" | {"title", "due"}
            title, due = (t["title"], t.get("due")) if isinstance(t, dict) else (t, None)
            self.store.add_task(OWNER, title, self.now, due)
        if setup.get("scheduler_digest"):           # the separate scheduler process: its messages only reach the log
            from sebastian.notify import LoggingNotifier, StubNotifier
            from sebastian.scheduler import Scheduler, State
            from datetime import time as _t
            Scheduler(_t(8, 30), _t(20, 0), lambda now: self.hub.events(now, now + timedelta(days=2)),
                      LoggingNotifier(StubNotifier(), self.store, OWNER, lambda: self.now), State(None)).tick(self.now)
        if setup.get("import"):                     # a CLI import preview, as `sebastian import-plan --send` makes it
            for r in self.assistant.propose_import(_drafts(setup["import"]), self.now):
                self.api.send(OWNER, r)
                self.store.log(OWNER, r, "out", self.now)      # like `sebastian import-plan --send`: the message is in the log (ADR 0010)

    # -- contract --
    def deliver(self, msgs: list[Msg]) -> None:
        for m in msgs:
            self.api.inbox.append({"update_id": m.update_id, "message": {"message_id": m.update_id, "date": m.ts,
                                   "chat": {"id": m.chat, "type": "private"}, "from": {"id": m.chat}, "text": m.text}})
        self.bot.step(poll_timeout=0)

    def sent(self, chat: int) -> list[str]:
        return [t for c, t in self.api.sent if c == chat]

    def advance(self, minutes: int) -> None:
        self.now += timedelta(minutes=minutes)

    def model(self, up: bool) -> None:
        self.flaky.up = up

    def network(self, up: bool) -> None:
        self.api.fail = not up

    def preview(self, chat: int):
        row = self.store.open_pending(chat)
        if row is None:
            return None
        args = json.loads(row["args_json"])
        if row["intent"].startswith("list_") or row["intent"] == "task_write":
            return None             # list previews are Sebastian's own store, not records: open_question() covers I10 for them
        if row["intent"] == "add_event":
            return [{"title": d["title"], "start": d["start"], "end": d["end"]} for d in args["drafts"]]
        if row["intent"] == "edit_event":
            ch = args["changes"]
            return [{"id": args["event_id"], **{k: ch[k] for k in ("title", "start", "end", "reminder_minutes") if k in ch}}]
        return [{"id": args["event_id"], "_delete": True}]

    def tasks(self) -> list[dict]:
        """Every to-do with its status and due day (training-loop expectations: tasks_open_has, tasks_done_has, tasks_due, ...)."""
        return [{"title": r["title"], "status": r["status"], "due": r["due"]}
                for r in self.store.c.execute("SELECT title, status, due FROM tasks WHERE chat_id=? ORDER BY id", (OWNER,)).fetchall()]

    def writes(self) -> list[dict]:
        return [{"chat": OWNER, "id": e.event_id, "title": e.title, "start": e.start.isoformat(), "end": e.end.isoformat(),
                 "reminder_minutes": e.reminder_minutes} for e in self.hub._events]

    def state_key(self, chat: int) -> str:
        conv = {k: v for k, v in (self.store.get_conversation(chat) or {}).items() if k not in ("at", "turns", "ask_n")}
        row = self.store.open_pending(chat)
        return json.dumps([conv, row["args_hash"] if row else None, len(self.writes())], sort_keys=True)

    def open_question(self, chat: int) -> bool:
        pending = self.store.open_pending(chat)
        return (self.store.get_conversation(chat) is not None or (pending is not None and (pending["intent"].startswith("list_") or pending["intent"] == "task_write"))
                or self.store.get_meta("agent_q", "0") == "1")

    def is_quote(self, text: str) -> bool:
        from sebastian import i18n
        return i18n.is_recall(text)

    # -- Sebastian policy: a bare yes on a DELETE preview is only a hint; 'sil' / 'evet sil' / an approval delete
    def expected_kind(self, kind: str, text: str, preview) -> str:
        if kind == "confirm" and preview and any(p.get("_delete") for p in preview) and dialogue.parse_confirm(text, True) != "yes":
            return "neutral"
        return kind


class Flaky:
    """A model that is unavailable (usage limit) until switched on; then it answers by message keyword, else chit-chat."""
    name, model = "flaky", "flaky-1"

    def __init__(self, intents: dict, up: bool):
        self.intents, self.up = intents, up

    def parse(self, message, context):
        if not self.up:
            raise nlmod.ProviderError("usage-limit", reason="usage")
        if context.get("dialogue"):
            return {"ops": [{"op": "chitchat"}]}
        for key, intent in self.intents.items():
            if key in message:
                return dict(intent)
        return {"intent": "chitchat"}


class _Down:
    name, model = "down", "down-1"

    def parse(self, message, context):
        raise RuntimeError("down")


def make_adapter(setup: dict) -> SebastianAdapter:
    return SebastianAdapter(setup)
