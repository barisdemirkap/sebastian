"""The owner's chat assistant (ADR 0004). One message in, plain Turkish replies out.

Flow: owner check -> commands -> 'evet' gate -> ask-until-sure state -> intent (language model, else rules) ->
reads are computed in code; writes only become a stored pending action with a code-rendered preview. Nothing is
written to the calendar except by `_execute`, which runs the STORED arguments after an explicit 'evet'."""
from __future__ import annotations
import difflib
import hashlib
import json
import re
import time as _time
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import birthdays, clauses as clausemod, dialogue, fuzzytime, health, i18n, lists as listmod, nl as nlmod, planread, purchase as purchasemod, readytime, rules, tasks as taskmod
from .http import WriteAccessDenied
from .merge import merge
from .models import Event, EventDraft
from .plugins import Plugins
from .providers import WriteDisabled
from .planning import PlanningMixin
from .statements import StatementsMixin
from .store import Store
from .textutil import clean, default_duration, fold, same_stem, similar_title, title_match, title_words
from .timeparse import find_date, find_time, names_today_weekday

CLARIFY_TTL = timedelta(minutes=15)
PROBE_GAP = timedelta(minutes=2)     # while the retry queue is non-empty the model is tried again at most this often (ADR 0021)
MAX_TURNS = 5
DEFAULT_DURATION = 60
CHORE_DURATION = 15
ITEM_BUTTONS_MAX = 20     # per-item tap buttons under a task or list view (ADR 0025); the rest stay in the text
ITEM_LABEL_MAX = 40
CANCEL_WORDS = {"hayir", "vazgec", "iptal", "bos ver", "bosver", "vazgectim", "cancel"}
RECALL_AT = re.compile(r"\bsaat\s*(\d{1,2})(?:[:.](\d{2}))?\s*(?:te|ta|de|da)?(?:ki)?\s*mesaj\w*\s*(?:i\s*)?(?:oku|goster|tekrar)")
RECALL_LAST = re.compile(r"\baz once ne (?:ded|yazd|gonderd)\w*|\bson mesaj\w*\s*(?:i\s*)?(?:oku|goster|tekrar)\w*|\bson yazdigin\w*")
DAYPART = re.compile(r"\b(sabah|gece|ogle|ogleden|aksam|ikindi)")
EXPLICIT_CLOCK = re.compile(r"\d{1,2}[:.]\d{2}")


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def draft_to_dict(d: EventDraft) -> dict:
    x = asdict(d)
    x["start"], x["end"] = d.start.isoformat(), d.end.isoformat()
    return x


def dict_to_draft(x: dict) -> EventDraft:
    return EventDraft(**{**x, "start": _dt(x["start"]), "end": _dt(x["end"])})


class Assistant(PlanningMixin, StatementsMixin):
    def __init__(self, hub, store: Store, nl: nlmod.NLService, tz: ZoneInfo, owner_chat: int, clock=None,
                 default_lead: int = 30, agent=None, readonly: bool = False, plugins: Plugins | None = None):
        self.hub, self.store, self.nl, self.tz, self.owner = hub, store, nl, tz, owner_chat
        self.plugins = plugins or Plugins()   # ADR 0005: which features are switched on (all by default)
        self.agent = agent                 # optional AgentMode (ADR 0019); None keeps the dialogue manager alone
        self.readonly = readonly           # SEBASTIAN_NL=readonly: answers reads only, never writes
        self.turn_no = 0                   # owner messages handled so far (the scripted eval model keys its steps on it)
        self.clock = clock or (lambda: datetime.now(tz))
        self.default_lead = default_lead
        self.shown: int | None = None      # id of the pending action whose preview the last turn rendered (ADR 0007)
        self._flags: list[tuple[str, str]] = []   # health signals raised while handling the current message (ADR 0009)
        self._restated = False                    # this turn answered the owner's impatience itself (ADR 0014): no way-out menu
        self.shown_tasks = False                  # the last turn rendered the task list (per-task tap buttons, ADR 0025)
        self.shown_list: str | None = None        # key of the list whose view the last turn rendered (inline buttons, ADR 0016)
        self._raw = ""                            # the current message with its newlines (ADR 0017)
        self._outage = False                      # the language model could not be used this turn (ADR 0021)
        self._replaying = False                   # a queued message is being read again: never answered by the rules
        self._replay_failed = False
        self._outage_reset: int | None = None
        self._outage_reason = "other"
        self._read = False                        # this turn was a read-only query: repeating its answer is legitimate (no repeat guard)
        self._acted = False                       # this turn did something (task changed, preview shown, write): never a failure menu after it
        self._in_clauses = False
        self.assist = None                 # ADR 0023: model assist for statements (set by the bot; None in tests unless injected)
        self._noop = False                        # the executor found the stored change equal to the current state (ADR 0015)

    # ------------------------------------------------------------------ entry
    def handle(self, chat_id: int, text: str, voice: bool = False) -> list[str]:
        if chat_id != self.owner:
            return []                      # single user: strangers get no reply at all
        now = self.clock()
        self.turn_no += 1
        self.shown = None
        self.shown_list = None
        self.shown_tasks = False
        self._flags = []
        self._restated = False
        self._outage = False
        self._read = False
        self._acted = False
        self._raw = text                   # keeps newlines: 'clean' flattens them, the purchase classifier splits on them
        self._rotate_address()
        try:
            p0 = self.store.open_pending(self.owner)
            out = self._no_repeat(self._route(text, now), text)
            out += self._drain_actions(now)
            out += self._drain_queue(now)
            if p0 and (self.store.get_pending(p0["id"]) or {"status": ""})["status"] in ("expired", "tampered"):
                self._flag("S3", "preview-" + self.store.get_pending(p0["id"])["status"])
            return [i18n.one_address(r) for r in self._after_turn(text, now, out)]
        except Exception as exc:  # noqa: BLE001 - never a traceback to the chat
            self.store.audit("error", {"type": type(exc).__name__}, now, self.owner)
            return [i18n.t("readonly_mode" if self.agent is not None else "oops")]   # last rung of the fallback chain

    def _route(self, text: str, now: datetime) -> list[str]:
        """Agent mode first when it is on and healthy (ADR 0019), else the dialogue manager; commands stay in code."""
        if self.readonly:
            return self._readonly_turn(clean(text, 2000), now)
        if self.agent is not None and text.strip() and not text.lstrip().startswith("/"):
            try:
                out = self.agent.turn(clean(text, 2000), now)
            except Exception as exc:  # noqa: BLE001 - an agent bug falls back to the manager, never to the chat
                self.store.audit("error", {"type": type(exc).__name__, "where": "agent"}, now, self.owner)
                out = None
            if out is not None:
                return out
        return self._handle(clean(text, 2000), now)

    def _readonly_turn(self, text: str, now: datetime) -> list[str]:
        """Read-only mode: commands and calendar reads only; every write wording gets the polite refusal."""
        low = fold(text).strip(" .!?,;")
        if low.startswith("/"):
            return self._command(low, now)
        p = rules.parse(text, now.date())
        if p["intent"] in ("list_events", "birthdays", "help", "chitchat"):
            return self._dispatch(p, text, now)
        return [i18n.t("readonly_mode")]

    def _off(self, plugin: str) -> list[str]:
        """The friendly refusal for a switched-off plugin (ADR 0005)."""
        self._read = self._acted = True
        return [i18n.t("plugin_off", name=i18n.PLUGIN_TITLES[plugin], plugin=plugin)]

    def _plugin_gate(self, text: str, now: datetime) -> list[str] | None:
        """Wording-level refusal. Only runs when something is switched off, so the default path is untouched. Best effort: the
        writes themselves are also refused at confirm time (`_confirm`) and in the agent tools."""
        if not self.plugins.disabled:
            return None
        if not self.plugins.enabled("tasks"):
            cl = clausemod.interpret(text, now.date()) or []
            if (taskmod.parse(text) or taskmod.parse(planread.fix(text)) or taskmod.is_remaining(text)
                    or planread.is_missing_tasks_question(text) or any(c["kind"] in ("done", "partial", "add") for c in cl)):
                return self._off("tasks")
        if not self.plugins.enabled("lists") and listmod.parse(text):
            return self._off("lists")
        if not self.plugins.enabled("calendar") and (
                planread.is_plan_read(text) or rules.parse(text, now.date())["intent"] in (
                    "add_event", "edit_event", "delete_event", "move_event", "list_events", "birthdays")):
            return self._off("calendar")
        return None

    def _rotate_address(self) -> None:
        """Persona (ADR 0018): the form of address rotates once per owner message; the counter lives in the store."""
        n = int(self.store.get_meta("addr_n", "0") or 0)
        i18n.set_rotation(n)
        self.store.set_meta("addr_n", n + 1)

    # ------------------------------------------------------------------ conversation health (ADR 0009)
    def _flag(self, signal: str, detail: str = "") -> None:
        self._flags.append((signal, detail))

    def _after_turn(self, text: str, now: datetime, out: list[str]) -> list[str]:
        """Record the signals of this turn; on an incident add ONE short way out (max one per 30 minutes), always last
        so the preview keeps its keyboard. The owner is the only user, so the menu is also the owner's notice."""
        if not out or text.lstrip().startswith("/"):
            flags, self._flags = self._flags, []
            for sig, detail in flags:
                health.record(self.store.c, self.owner, sig, detail, now)
            return out
        run = int(self.store.get_meta("h_unclear", "0") or 0)
        new_run = run + 1 if any(health.is_unclear(r) for r in out) else 0
        if new_run != run:
            self.store.set_meta("h_unclear", new_run)
        if new_run == health.UNCLEAR_RUN:
            self._flag("S2", f"run={new_run}")
        frustrated = health.is_frustrated(text)
        if frustrated:
            self._flag("S4", "frustration")
        return self._incident(now, new_run, frustrated and not self._restated, out)

    def _incident(self, now: datetime, unclear_run: int, frustrated: bool, out: list[str]) -> list[str]:
        flags, self._flags = self._flags, []
        for sig, detail in flags:
            health.record(self.store.c, self.owner, sig, detail, now)
        if self._acted or (self.shown is not None and self.agent is None):
            return out                            # a successful action or a fresh preview is never followed by the way-out menu
        if self._outage or self._outage_active():
            return out                            # an outage is told once in its own words, never as the way-out menu (ADR 0021)
        recent = health.signals_since(self.store.c, self.owner, now - health.WINDOW)
        if not health.is_incident(recent, unclear_run, frustrated):
            return out
        last = self.store.get_meta("h_incident_at")
        if last and now - datetime.fromisoformat(last) < health.COOLDOWN:
            return out
        self.store.set_meta("h_incident_at", now.isoformat(timespec="seconds"))
        menu = i18n.t("health_menu")
        if self.agent is not None and self.shown is not None and out:
            return out[:-1] + [menu] + out[-1:]      # agent mode: the preview stays the LAST bot message, so a yes can still qualify
        return out + [menu]

    def note_slow(self, ms: int) -> list[str]:
        """S6, called by the bot loop with the time one update took; returns the menu when it makes an incident."""
        if ms <= health.SLOW_MS:
            return []
        self._flag("S6", f"ms={ms}")
        return self._incident(self.clock(), 0, False, [])

    def _report(self, now: datetime) -> list[str]:
        last = self.store.get_meta("h_report_at")
        if last and now - datetime.fromisoformat(last) < health.REPORT_GAP:
            return [i18n.t("report_again")]
        self.store.set_meta("h_report_at", now.isoformat(timespec="seconds"))
        health.record(self.store.c, self.owner, "REPORT", "owner-flag", now)
        return [i18n.t("report_thanks")]

    def _health(self, now: datetime) -> list[str]:
        c = health.counts(self.store.c, now - timedelta(hours=24)).get(self.owner)
        return [i18n.t("health_title", rows=health.format_counts(c))] if c else [i18n.t("health_none")]

    def _no_repeat(self, replies: list[str], text: str) -> list[str]:
        """A single reply identical to what the bot just said gets a changing lead, so it never repeats word for word.
        Commands answer the same way every time and are exempt."""
        if len(replies) != 1 or text.lstrip().startswith("/") or self._read:
            return replies                        # a repeated read-only query is legitimate: same answer, no lead, no signal
        base = replies[0]
        menu = i18n.t("health_menu")
        outs = [r["text"] for r in self.store.recent_messages(self.owner, 6) if r["direction"] == "out" and r["text"] != menu]
        if not outs:
            return replies
        if outs[-1] == base:
            self._flag("S1", "repeat-guard")
            return [i18n.REPEAT_LEADS[0] + base]
        for i, lead in enumerate(i18n.REPEAT_LEADS):
            if outs[-1] == lead + base:
                self._flag("S1", "repeat-guard")
                return [i18n.REPEAT_LEADS[(i + 1) % len(i18n.REPEAT_LEADS)] + base]
        return replies

    def _handle(self, text: str, now: datetime) -> list[str]:
        if not text:
            return []
        today = now.date()
        low = fold(text).strip(" .!?,;")
        if low.startswith("/"):
            return self._command(low, now)
        if (out := self._plugin_gate(text, now)) is not None:
            return out                       # a wording for a switched-off plugin gets the friendly refusal (ADR 0005)
        if (recalled := self._recall(low, now)) is not None:
            return recalled                  # reading back changes nothing: an open preview or question stays
        if (ref := self._task_number_ref(text)) is not None:
            text, low = ref, fold(ref).strip(" .!?,;")      # '2 yapildi' after a numbered task list: the second task
        if planread.is_missing_tasks_question(text):
            return [i18n.t("plan_tasks_ack")] + self._list({"range": "today"}, now)     # 'why are my tasks not in the plan': own it, show them
        if (tk := taskmod.parse(text) or taskmod.parse(planread.fix(text))) and tk["kind"] == "list":
            return self._task_list()         # a read: an open preview or question stays ('doger gorevlerim' too)
        if (out := self._tentative_plan(text, now)) is not None:
            return out                       # 'bu gorevleri de planla': one tentative plan preview (ADR 0024)
        if (out := self._statements(text, now)) is not None:
            return out                       # statements about existing items, clause by clause (training loop)
        if taskmod.is_remaining(text):
            return self._remaining(now)      # 'neler kaldi': what is left of today, a read
        if (out := self._task_bulk(text, now)) is not None:
            return out                       # whole-list task statements never become an answer or a new task
        if (ls := listmod.parse(text)) and (out := self._list_command(ls, now)) is not None:
            return out                       # lists live in Sebastian's own store, not the calendar (ADR 0016)
        pending = self.store.open_pending(self.owner)
        if pending:
            out = self._with_pending(pending, text, low, now)
            if out is not None:
                return out
            pending = None
        elif low == "evet" or low == "evet sil":
            if (conv := self.store.get_conversation(self.owner)) and now - _dt(conv["at"]) <= CLARIFY_TTL:
                return [i18n.t("reask_open", q=conv.get("question") or self._reask(conv))]   # never 'nothing pending' (I10)
            return self._confirm(None, low, now)
        if low in CANCEL_WORDS:
            self.store.clear_conversation(self.owner)
            return [i18n.t("cancelled")]
        conv = self.store.get_conversation(self.owner)
        if conv and now - _dt(conv["at"]) > CLARIFY_TTL:
            self.store.clear_conversation(self.owner)      # a stale question is dropped silently (ADR 0014)
            conv = None
        if dialogue.is_impatient(text):                    # a presence check is never an answer
            return [i18n.t("present_ask", q=conv.get("question") or self._reask(conv))] if conv else [i18n.t("present")]
        if conv and conv.get("awaiting") == "list":
            # list mode: the list is its own topic; a calendar/plan wording leaves it, anything else is list content
            p = rules.parse(text, today)
            if p["intent"] in ("list_events", "birthdays") or find_time(text) or self._is_new_intent(text) \
                    or planread.is_plan_read(text) or fold(text).split()[:1] in (["iptal"], ["vazgectim"], ["cancel"]):
                self.store.clear_conversation(self.owner)
                conv = None
            else:
                items = listmod.split_items(re.sub(r"\s+(?:ekle|ekler misin|ekleyelim|ekleyin|koy|yaz)\w*$", "", text.strip(), flags=re.I))
                self.store.set_conversation(self.owner, {**conv, "turns": 1, "at": now.isoformat()})     # list mode stays open
                return self._list_command({"kind": "add", "list": conv["list"], "items": items}, now) if items else [conv["question"]]
        if conv:
            # a read/help request, or a complete new add, is a new request and not an answer
            p = rules.parse(text, today)
            if p["intent"] in ("list_events", "birthdays", "chitchat") or self._is_new_intent(text) or (
                    p["intent"] == "add_event" and p.get("title") and p.get("date") and conv["awaiting"] in ("title", "date", "time")):
                self.store.clear_conversation(self.owner)
            else:
                return self._answer(conv, text, now)
        return self._new(text, now)

    @staticmethod
    def _is_new_intent(text: str) -> bool:
        """A self-contained statement (an explicit to-do wording, or several done/to-do clauses) is never the answer to an open
        question: the question is dropped and the statement is handled as a new request."""
        tk = taskmod.parse(text)
        if tk and tk["kind"] in ("add", "done", "remove", "list"):
            return True
        parts = taskmod.split_clauses(text)
        return len(parts) >= 2 and sum(taskmod.has_marker(p) for p in parts) >= 2

    # ------------------------------------------------------------------ read back (ADR 0010)
    def _recall(self, low: str, now: datetime) -> list[str] | None:
        """'az once ne dedin' / 'saat 3 teki mesaji oku': show what the bot itself sent (digests, reminders, previews are
        all in the message log). None when the message is not such a request."""
        m = RECALL_AT.search(low)
        if not m and not RECALL_LAST.search(low):
            return None
        menu = i18n.t("health_menu")
        outs = [r for r in self.store.recent_messages(self.owner, 200)
                if r["direction"] == "out" and r["text"] != menu and not i18n.is_recall(r["text"])]
        if m:
            h = int(m[1])
            hours = {h, h + 12} if h < 12 else {h}
            hit = [r for r in outs if now - _dt(r["at"]) <= timedelta(hours=48) and _dt(r["at"]).astimezone(self.tz).hour in hours]
            if not hit:
                return [i18n.t("recall_none_at", hours=" veya ".join(f"{x:02d}:00" for x in sorted(hours)))]
            r = hit[-1]
            return [i18n.t("recall_at", time=f"{_dt(r['at']).astimezone(self.tz):%H:%M}", text=r["text"][:3000])]
        if not outs:
            return [i18n.t("recall_none")]
        return [i18n.t("recall_last", text=outs[-1]["text"][:3000])]

    # ------------------------------------------------------------------ pending preview (ADR 0006)
    def _reoffer(self, row) -> str:
        return i18n.t("pending_reoffer_delete" if row["intent"] == "delete_event" else "pending_reoffer")

    def _with_pending(self, row, text: str, low: str, now: datetime) -> list[str] | None:
        """Every message while a preview is open. Only an explicit cancel, a confirmation, TTL expiry, or a new write
        request the owner agrees to park closes it; anything else is answered and the preview stays. Returns None
        when the preview has expired and the message must be handled as a fresh request."""
        delete = row["intent"] == "delete_event"
        decision = dialogue.parse_confirm(text, delete)
        conv = self.store.get_conversation(self.owner)
        if Store.pending_state(row, now) == "expired" and decision not in ("yes", "weak"):
            self.store.close_pending(row["id"], "expired")
            self.store.clear_conversation(self.owner)
            return None
        if Store.pending_state(row, now) == "expired":
            return self._reshow(row, now)                # a confirmation of an expired preview: say so, show it again
        if conv and conv.get("awaiting") == "park" and conv.get("pending_id") == row["id"]:
            self.store.clear_conversation(self.owner)
            if decision == "yes":
                self.store.close_pending(row["id"], "superseded")
                return self._new(conv["text"], now)
            if decision != "no":                 # anything but a clear yes keeps the preview
                return [i18n.t("park_declined")]
            return [i18n.t("park_declined")]
        if row["intent"] == "add_event" and (nd := json.loads(row["args_json"]).get("near_dup")) and (out := self._near_dup_reply(row, nd, text, now)) is not None:
            return out
        if decision == "yes":
            return self._confirm(row, "evet sil" if delete else "evet", now)
        if decision == "weak":
            return self._confirm(row, "evet", now)
        if decision == "no":
            self.store.close_pending(row["id"], "cancelled")
            self.store.clear_conversation(self.owner)
            return [i18n.t("cancelled")]
        if dialogue.is_impatient(text):
            return [i18n.t("present_pending", reoffer=self._reoffer(row))]
        return self._pending_other(row, text, now)

    NEAR_MOVE = re.compile(r"^(?:evet |lutfen |tamam )?(?:tasi\w*|degistir\w*|guncelle\w*|onu tasi\w*|yerine\w*|yerini degistir\w*)(?: lutfen)?$")
    NEAR_SEPARATE = re.compile(r"^(?:evet |tamam )?(?:ayrica(?: ekle\w*)?|ayri(?: ekle\w*| olsun)?|ikisi de(?: kalsin| olsun)?|ekle\w*|iki(?:si)? de)$")

    def _near_dup_reply(self, row, nd: dict, text: str, now: datetime) -> list[str] | None:
        """The answer to 'move it, or add it separately?' (the add preview that mentions an existing near-duplicate): 'tasi' turns it
        into an edit preview of the existing event (nothing is written), 'ayrica' is a yes to the add. Anything else: None."""
        f = fold(text).strip(" .!?,;")
        if self.NEAR_SEPARATE.match(f):
            return self._confirm(row, "evet", now)
        if not self.NEAR_MOVE.match(f):
            return None
        e = self._event_by_id(nd["event_id"])
        d = dict_to_draft(json.loads(row["args_json"])["drafts"][0])
        if e is None:
            return None
        self.store.close_pending(row["id"], "superseded")
        s0 = d.start.astimezone(self.tz)
        changes = {"date": s0.date().isoformat(), "time": f"{s0:%H:%M}", "duration_min": int((d.end - d.start).total_seconds() // 60)}
        return self._propose_target({"intent": "edit_event", "match": e.title, "date": None, "scope": "this", "changes": changes}, e, now)

    def _reshow(self, row, now: datetime) -> list[str]:
        """The owner confirmed a preview that has expired (ADR 0014): nothing is written; the SAME stored parameters are
        shown again with a fresh time limit and need a fresh yes. A past-only add is not worth showing."""
        args = json.loads(row["args_json"])
        self.store.close_pending(row["id"], "expired")
        self.store.audit("pending_expired", {"id": row["id"]}, now, self.owner)
        objs: list = []
        if row["intent"] == "add_event":
            objs = [dict_to_draft(x) for x in args["drafts"]]
            if all(d.start <= now for d in objs):
                return [i18n.t("pending_expired")]
        ttl = datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"])
        shown = self._propose(row["intent"], args, objs, now, ttl)
        return [i18n.t("preview_expired_reshow") + "\n" + shown[0]] + shown[1:]

    def _pending_other(self, row, text: str, now: datetime) -> list[str]:
        """A message that is not a plain yes/no while a preview is open: ONE model call when NL is on (strict
        operations), else deterministic rules. The preview is only changed by validated operations applied in code."""
        args = json.loads(row["args_json"])
        is_add = row["intent"] == "add_event"
        drafts = args.get("drafts", []) if is_add else []
        body = dialogue.split_edit(text) or text
        ops = self._model_ops(row, drafts, body, now) if self._nl_ok(now) else None
        if ops is None:
            ops = self._rule_ops(drafts, body, now)
        if ops is not None and not is_add:
            ops = [o for o in ops if o["op"] in ("read", "cancel", "chitchat", "new_request")] or None
        if ops is None:
            return [i18n.t("pending_unclear", reoffer=self._reoffer(row))]
        out = dialogue.apply_ops(drafts, ops, self.tz)
        replies = [i18n.t(k) for k in dict.fromkeys(out.errors)]
        for q in out.reads:
            replies += self._birthdays(now) if q == "birthdays" else self._list({"range": q}, now)
        if out.changed:
            ttl = datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"])
            objs = [dict_to_draft(x) for x in out.drafts]
            fz = fuzzytime.find(body)
            note = i18n.t("p_fuzzy", t=fuzzytime.clock(fz), said=fz.said) if fz and fz.start else None
            return [i18n.t("dlg_updated")] + replies + self._propose("add_event", {"drafts": out.drafts}, objs, now, ttl, note=note)
        if out.cancel:
            self.store.close_pending(row["id"], "cancelled")
            self.store.clear_conversation(self.owner)
            return [i18n.t("cancelled")]
        if out.new_request:
            self.store.set_conversation(self.owner, {"awaiting": "park", "pending_id": row["id"], "text": text, "turns": 1,
                                                     "at": now.isoformat()})
            return [i18n.t("park_ask")]
        if out.chitchat and not replies:
            replies.append(i18n.t("greeting_pending"))
        return replies + [self._reoffer(row)]

    def _rule_ops(self, drafts: list[dict], text: str, now: datetime) -> list[dict] | None:
        p = rules.parse(text, now.date())
        if p["intent"] in ("list_events", "birthdays"):
            q = "birthdays" if p["intent"] == "birthdays" else p.get("range", "today")
            return [{"op": "read", "query": q if q in dialogue.READ_QUERIES else "upcoming"}]
        if p["intent"] in ("help", "chitchat"):
            return [{"op": "chitchat"}]
        if drafts and (ops := dialogue.rules_ops(text, now.date(), len(drafts))):
            return ops
        if p["intent"] == "add_event" and set(fold(p.get("title") or "").split()) <= i18n.EDIT_FILLER:
            return None                       # an edit without a target ("yarin olsun" on several events): ask, never park
        if (p["intent"] == "add_event" and p.get("title") and p.get("date")) or p["intent"] in ("edit_event", "delete_event"):
            return [{"op": "new_request"}]
        return None

    def _model_ops(self, row, drafts: list[dict], text: str, now: datetime) -> list[dict] | None:
        ctx = self._context(now)
        ctx["draft"] = [{"n": i, "title": d["title"], "date": _dt(d["start"]).date().isoformat(),
                         "time": None if d["all_day"] else f"{_dt(d['start']):%H:%M}",
                         "duration_min": int((_dt(d["end"]) - _dt(d["start"])).total_seconds() // 60),
                         "reminder_min": d.get("reminder_minutes"), "location": d.get("location") or None,
                         "notes": d.get("notes") or None} for i, d in enumerate(drafts, 1)]
        if not drafts:
            ctx["draft"] = [{"n": 1, "kind": row["intent"]}]
        recent = self.store.recent_messages(self.owner, 9)
        if recent and recent[-1]["direction"] == "in" and recent[-1]["text"] == text:
            recent = recent[:-1]
        ctx["recent"] = [{"who": "owner" if m["direction"] == "in" else "bot", "text": clean(m["text"], 200)} for m in recent[-8:]]
        ctx["last_question"] = next((x["text"] for x in reversed(ctx["recent"]) if x["who"] == "bot"), "")
        self.store.nl_bump(now.date().isoformat(), self.owner)
        raw, provider, model, ms = self.nl.dialogue(text, ctx)
        err = False
        ops = None
        if raw is not None:
            try:
                ops = dialogue.validate_ops(raw, text, now.date(), len(drafts))
            except nlmod.SchemaError:
                err = True
        self.store.audit("nl_dialogue", {"provider": provider, "model": model, "ms": ms, "schema_error": err,
                                         "ops": [o["op"] for o in ops or []]}, now, self.owner)
        if ops is None:
            self._flag("S5", "schema" if err else "chain-failed")
        return ops

    # ------------------------------------------------------------------ inline buttons (ADR 0007)
    def keyboard(self, pid: int | None = None) -> dict | None:
        """Inline keyboard for the preview of pending action `pid` (default: the one the last turn rendered)."""
        if pid is None and self.shown is None:
            return self._task_keyboard() if self.shown_tasks else self._list_keyboard()
        row = self.store.get_pending(pid or self.shown) if (pid or self.shown) else None
        if row is None or row["status"] != "open":
            return None
        h = row["args_hash"][:8]

        def btn(label: str, act: str) -> dict:
            return {"text": i18n.t(label), "callback_data": f"{act}:{row['id']}:{h}"}
        if row["intent"] == "delete_event":
            return {"inline_keyboard": [[btn("btn_delete", "d"), btn("btn_cancel", "x")]]}
        if row["intent"] == "add_event":
            return {"inline_keyboard": [[btn("btn_save", "k"), btn("btn_edit", "e"), btn("btn_cancel", "x")]]}
        return {"inline_keyboard": [[btn("btn_save", "k"), btn("btn_cancel", "x")]]}

    def callback(self, chat_id: int, action: str, pid: int, h8: str) -> tuple[str | None, list[str]]:
        """A button tap: (alert text or None, replies). Verified in code against the STORED, current, open, unexpired,
        unchanged preview; anything else is the stale alert and changes nothing. Save/delete runs the typed-'evet' path."""
        if chat_id != self.owner:
            return None, []
        now = self.clock()
        self.shown = None
        self.shown_list = None
        self.shown_tasks = False
        self._read = False
        self._acted = False
        self._rotate_address()
        if action in ("a", "c"):
            return self._list_callback(action, pid, h8, now)
        if action in ("i", "t"):
            return self._item_callback(action, pid, h8, now)
        if self.readonly:
            return i18n.t("readonly_mode"), []
        row = self.store.open_pending(self.owner)
        allowed = {"delete_event": "dx", "edit_event": "kx", "add_event": "kex", "list_add": "kx", "list_remove": "kx", "list_clear": "kx", "edit_events": "kx", "task_write": "kx"}
        if (row is None or row["id"] != pid or not h8 or row["args_hash"][:8] != h8 or Store.pending_state(row, now) != "ok"
                or action not in allowed.get(row["intent"], "")):
            return i18n.t("btn_stale"), []
        try:
            if action == "x":
                self.store.close_pending(pid, "cancelled")
                self.store.clear_conversation(self.owner)
                return None, [i18n.t("cancelled")] + self._drain_queue(now)
            if action == "e":
                return None, [i18n.t("btn_edit_prompt")]
            out = self._confirm(row, "evet sil" if action == "d" else "evet", now)
            return None, out + self._drain_queue(now)
        except Exception as exc:  # noqa: BLE001
            self.store.audit("error", {"type": type(exc).__name__}, now, self.owner)
            return None, [i18n.t("oops")]

    # ------------------------------------------------------------------ commands
    def _command(self, low: str, now: datetime) -> list[str]:
        cmd = low.split()[0].split("@")[0]
        cmd = i18n.COMMAND_ALIASES.get(cmd, cmd)          # English spellings of the commands map to the canonical ones
        if (off := self.plugins.blocked("commands", cmd)):
            return self._off(off)
        if cmd in ("/start",):
            return [i18n.t("start")]
        if cmd in ("/yardim", "/help"):
            return [i18n.t("help_short")]
        if cmd == "/komutlar":
            return [i18n.t("commands_full")]
        if cmd == "/rehber":
            return [i18n.t("guide")]
        if cmd == "/bugun":
            return self._list({"range": "today"}, now)
        if cmd == "/yarin":
            return self._list({"range": "tomorrow"}, now)
        if cmd == "/hafta":
            return self._list({"range": "week"}, now)
        if cmd == "/gorevler":
            return self._task_list()
        if cmd in ("/liste", "/alinacaklar"):
            return self._list_show(i18n.DEFAULT_LIST, now)
        if cmd == "/dogumgunleri":
            return self._birthdays(now)
        if cmd == "/bildir":
            return self._report(now)
        if cmd == "/saglik":
            return self._health(now)
        if cmd == "/bekleyenler":
            return self._queue_command(low.split()[1:] and low.split()[1] or "", now)
        if cmd == "/ajan":
            return self._agent_command(low.split()[1:] and low.split()[1] or "")
        if cmd == "/iptal":
            p = self.store.open_pending(self.owner)
            if p:
                self.store.close_pending(p["id"], "cancelled")
            self.store.clear_conversation(self.owner)
            return [i18n.t("cancelled")]
        return [i18n.t("unknown_command")]

    def _agent_command(self, arg: str) -> list[str]:
        """/ajan, /ajan kapat, /ajan ac: the instant rollback switch of agent mode (ADR 0019). Nothing is lost: an open draft
        is an ordinary pending action that both modes understand."""
        if self.agent is None:
            return [i18n.t("agent_status_unavailable")]
        if arg in ("kapat", "off"):
            self.agent.set_switch(False)
            return [i18n.t("agent_turned_off")]
        if arg in ("ac", "on"):
            self.agent.set_switch(True)
            return [i18n.t("agent_turned_on")]
        return [i18n.t("agent_status_on" if self.agent.switched_on() else "agent_status_off")]

    # ------------------------------------------------------------------ classification
    def _context(self, now: datetime) -> dict:
        try:
            evs = [e for e in merge(self.hub.events(now.replace(hour=0, minute=0, second=0, microsecond=0),
                                                    now + timedelta(days=14))) if e.calendar != "birthdays"]
        except Exception:  # noqa: BLE001
            evs = []
        return nlmod.build_context(now.date(), str(self.tz), [
            {"title": e.title, "date": e.start.astimezone(self.tz).date().isoformat(),
             "time": "" if e.all_day else f"{e.start.astimezone(self.tz):%H:%M}"} for e in evs])

    def _nl_ok(self, now: datetime) -> bool:
        return self.nl.enabled and self.store.nl_calls(now.date().isoformat(), self.owner) < self.nl.daily_cap

    def _classify(self, text: str, now: datetime, clarification: dict | None = None, earlier: str = "") -> tuple[dict | None, bool]:
        """(intent or None, schema_error). Language model first (consent + cap), then the minimal rules."""
        if self._nl_ok(now):
            if self._outage_active() and not self._probe_due(now):
                self._outage = True               # the model failed moments ago: no 13 second wait for every message
                return None, False
            ctx = self._context(now)
            if clarification:
                ctx["clarification"] = clarification
            self.store.nl_bump(now.date().isoformat(), self.owner)
            r = self.nl.parse(text, ctx, now.date(), earlier)
            self.store.audit("nl", {"provider": r.provider, "model": r.model, "intent": (r.intent or {}).get("intent"),
                                    "ms": r.ms, "schema_error": r.schema_error}, now, self.owner)
            if r.intent or r.schema_error:
                self._outage_end()
            if r.intent:
                return r.intent, False
            if r.schema_error:
                self._flag("S5", "schema")
                return None, True
            self._outage_begin(now, r)
        return None, False

    # ------------------------------------------------------------------ simple mode and the retry queue (ADR 0021)
    def _outage_active(self) -> bool:
        return bool(self.store.get_meta("outage_since", ""))

    def _probe_due(self, now: datetime) -> bool:
        last = self.store.get_meta("outage_checked", "")
        return not last or now - _dt(last) >= PROBE_GAP

    def _outage_begin(self, now: datetime, r) -> None:
        """The whole chain failed (not a schema error): remember the outage; the S5 signal is raised once per outage."""
        self._outage, self._outage_reset, self._outage_reason = True, r.reset, r.reason or "other"
        self.store.set_meta("outage_checked", now.isoformat(timespec="seconds"))
        if not self._outage_active():
            self.store.set_meta("outage_since", now.isoformat(timespec="seconds"))
            self.store.set_meta("outage_why", f"{self._outage_reason}|{r.reset or ''}")
            self._flag("S5", "outage")

    def _outage_end(self) -> None:
        if self._outage_active():
            self.store.set_meta("outage_since", "")
            self.store.set_meta("outage_notified", "0")
            self.store.set_meta("outage_checked", "")

    def _outage_notice(self) -> list[str]:
        """The simple-mode notice: ONCE per outage, with the reason and, when the provider said so, the expected recovery."""
        if self.store.get_meta("outage_notified", "0") == "1":
            return []
        self.store.set_meta("outage_notified", "1")
        why, _, reset = (self.store.get_meta("outage_why", "other|") or "other|").partition("|")
        eta = ""
        if reset.isdigit():
            eta = i18n.t("outage_eta", time=f"{datetime.fromtimestamp(int(reset), self.tz):%H:%M}")
        return [i18n.t("outage_notice", why=i18n.t("outage_why_" + (why if why in ("usage", "network") else "other")), eta=eta)]

    def _safe_mode(self, text: str, now: datetime) -> list[str]:
        """The model is unavailable: only a message the rules read with high confidence and as ONE event is answered; the rest
        is kept in the retry queue and nothing is added, previewed or guessed."""
        lead = self._outage_notice()
        intent = rules.safe(text, now.date())
        if intent is not None:
            return lead + self._dispatch(intent, text, now)
        self.store.queue_add(self.owner, text, now)
        return lead + [i18n.t("outage_saved", n=len(self.store.queue_list(self.owner)))]

    def _queue_command(self, arg: str, now: datetime) -> list[str]:
        if arg in ("temizle", "sil", "clear"):
            n = self.store.queue_clear(self.owner, now)
            return [i18n.t("queue_cleared", n=n)] if n else [i18n.t("queue_empty")]
        rows = self.store.queue_list(self.owner)
        if not rows:
            return [i18n.t("queue_empty")]
        lines = [i18n.t("queue_head", n=len(rows))] + [i18n.t("queue_row", n=i, x=clean(r["text"], 120)) for i, r in enumerate(rows, 1)]
        return ["\n".join(lines + [i18n.t("queue_tail")])]

    def retry_tick(self, now: datetime) -> list[str]:
        """Called by the bot loop: when the queue is non-empty, nothing waits for the owner and the model may be back (a probe
        at most every PROBE_GAP), read the OLDEST queued message through the normal path. Replies are 'intro, preview...';
        the preview still needs its own confirmation and nothing is written here."""
        rows = self.store.queue_list(self.owner)
        if not rows or self.store.open_pending(self.owner) or self.readonly or not self.nl.enabled:
            return []
        conv = self.store.get_conversation(self.owner)
        if conv and now - _dt(conv["at"]) <= CLARIFY_TTL:
            return []
        if self._outage_active() and not self._probe_due(now):
            return []
        row = rows[0]
        self.shown = self.shown_list = None
        self.shown_tasks = False
        self._flags, self._outage, self._restated = [], False, False
        self._raw, self._replaying, self._replay_failed = row["text"], True, False
        try:
            self._rotate_address()
            out = self._new(clean(row["text"], 2000), now)
        except Exception as exc:  # noqa: BLE001 - the message stays queued
            self.store.audit("error", {"type": type(exc).__name__, "where": "retry"}, now, self.owner)
            return []
        finally:
            self._replaying = False
            flags, self._flags = self._flags, []
            for sig, detail in flags:
                health.record(self.store.c, self.owner, sig, detail, now)
        if self._replay_failed or not out:
            return []                              # still unavailable: keep it, the next probe is in PROBE_GAP
        self.store.queue_close(row["id"], "done", now)
        self.store.audit("retry", {"queue_id": row["id"]}, now, self.owner)
        return [i18n.one_address(r) for r in [i18n.t("retry_intro", text=clean(row["text"], 300))] + out]

    def _new(self, text: str, now: datetime, skip_purchase: bool = False) -> list[str]:
        raw = self._raw if " ".join(self._raw.split()) == " ".join(text.split()) else text
        if planread.is_plan_read(text):
            return self._list({"range": "today"}, now)       # a plan read holds events and the open tasks (like the morning digest)
        if (out := self._move_existing(text, now)) is not None:
            return out                       # a mention of an existing item with a time or day moves it, before any create
        if not skip_purchase and (pu := purchasemod.classify(raw, now.date())) \
                and not (pu["done_like"] and self._finished_chore(pu["items"], now)):
            return self._purchase(pu, text, now)     # shopping wordings never reach the event path (ADR 0017)
        if (out := self._reschedule(text, now)) is not None:
            return out                       # an utterance about an existing event edits it before any add (ADR 0017)
        if (out := self._clauses(text, now)) is not None:
            return out                       # 'X yapildi, Y aksam cikacak': every clause is a task statement
        if (tk := taskmod.parse(text)) and (out := self._task(tk, now)) is not None:
            return out                       # explicit to-do wordings never reach the model (ADR 0013)
        intent, schema_err = self._classify(text, now)
        if schema_err:
            return [i18n.t("nl_unsure")]
        if intent is None and (self._outage or self._replaying):
            if self._replaying:
                self._replay_failed = True        # the model is still away: the message stays queued
                return []
            return self._safe_mode(text, now)
        if intent is None:
            intent = rules.parse(text, now.date())
        return self._dispatch(intent, text, now)

    def _clauses(self, text: str, now: datetime) -> list[str] | None:
        """A message of several clauses (done / to do / timed) is handled clause by clause and EVERY clause is answered. To-do
        additions go first (they need no answer); then each other clause in order. Only one preview or question can be open,
        so the clauses after it wait in a queue, are announced, and are handled as soon as the open one is answered."""
        if self._in_clauses:
            return None
        parts = taskmod.split_clauses(text)
        if len(parts) < 2:
            return None
        tks = [taskmod.parse(p) for p in parts]
        stated = [bool(t) or taskmod.has_marker(p) for p, t in zip(parts, tks)]
        if not all(stated) or any(t and t["kind"] in ("list", "remove") for t in tks) or sum(taskmod.has_marker(p) for p in parts) < 2 \
                and not all(tks):
            return None
        order = [i for i, t in enumerate(tks) if t and t["kind"] == "add"] + [i for i, t in enumerate(tks) if not (t and t["kind"] == "add")]
        out: list[str] = []
        waiting: list[str] = []
        self._in_clauses = True
        try:
            for i in order:
                if waiting or self.store.open_pending(self.owner) or self.store.get_conversation(self.owner):
                    waiting.append(parts[i])
                    continue
                r = (self._task(tks[i], now) if tks[i] else None) or self._new(parts[i], now)
                out += r or []
        finally:
            self._in_clauses = False
        if waiting:
            self.store.set_meta("clause_queue", json.dumps({"at": now.isoformat(), "parts": waiting}))
            out.append(i18n.t("clause_queued", x="; ".join(waiting)))
        return out or None

    def _drain_queue(self, now: datetime) -> list[str]:
        """The next waiting clause of a multi-clause message, once nothing is open any more (one at a time)."""
        raw = self.store.get_meta("clause_queue", "")
        if not raw or self.store.open_pending(self.owner) or self.store.get_conversation(self.owner):
            return []
        q = json.loads(raw)
        parts = q.get("parts") or []
        if not parts or now - _dt(q["at"]) > timedelta(hours=2):
            self.store.set_meta("clause_queue", "")
            return []
        self.store.set_meta("clause_queue", json.dumps({"at": q["at"], "parts": parts[1:]}) if parts[1:] else "")
        tk = taskmod.parse(parts[0])
        self._in_clauses = True
        try:
            out = (self._task(tk, now) if tk else None) or self._new(parts[0], now) or []
        finally:
            self._in_clauses = False
        return out

    def _best_guess(self, text: str, now: datetime) -> list[str] | None:
        """When truly unsure: the best reading of the owner's own words instead of a bare 'did not understand'. Chores and
        to-dos become tasks (reversible: 'gorevi kaldir'); anything else becomes an event draft whose missing date or
        time is then asked (the usual preview path)."""
        if (out := self._clauses(text, now)) is not None:
            return out
        words = fold(text).split()
        if len(words) < 2 or "?" in text or (taskmod.parse(text) or {}).get("kind") == "done":
            return None
        if taskmod.is_meta(text):
            return [i18n.t("meta_ask")]          # an instruction or comment: never guessed into a task or an event, asked instead
        if any(w.startswith(i18n.CHORE_STEMS + i18n.TODO_STEMS) for w in words):
            return self._task({"kind": "add", "title": taskmod._cap(text), "implicit": True}, now)
        title = rules._title(text, []) if any(w.startswith(i18n.APPOINTMENT_STEMS) for w in words) else None
        if not title:
            return None
        return self._add({"intent": "add_event", "title": title, "date": None, "time": None, "end_time": None, "duration_min": None,
                          "all_day": False, "reminder_min": None, "location": None, "notes": None, "recurrence": "", "tag": "",
                          "_orig": text}, text, now, 1)

    def _dispatch(self, intent: dict, text: str, now: datetime) -> list[str]:
        name = intent["intent"]
        if (off := self.plugins.blocked("intents", name)):
            return self._off(off)
        if name == "list_events":
            return self._list(intent, now)
        if name == "birthdays":
            return self._birthdays(now)
        if name == "help":
            return [i18n.t("help_short")]
        if name == "chitchat":
            return [i18n.t("greeting")]
        if name == "unclear":
            q = intent.get("question")
            if q:
                return self._ask_free(q, text, now)
            return self._best_guess(text, now) or [i18n.t("unclear_examples")]
        if name == "add_event":
            return self._add({**intent, "_orig": text}, text, now, 1)
        if name in ("edit_event", "delete_event"):
            return self._target(intent, now, text)
        return [i18n.t("unclear_examples")]

    # ------------------------------------------------------------------ purchase wordings (ADR 0017)
    def _finished_chore(self, items: list[str], now: datetime) -> bool:
        """'X bitti' is a finished chore (not an item that ran out) when X is a chore word, an open task or a near event."""
        return any(any(w.startswith(i18n.CHORE_STEMS + i18n.TODO_STEMS) for w in title_words(i)) or self._find_task(i)
                   or self._events_soon(i, now) for i in items)

    def _purchase(self, pu: dict, text: str, now: datetime) -> list[str]:
        """Clear purchase wording: straight onto the list. With a day or time next to it, ONE numbered choice."""
        if not pu["event_like"]:
            return self._list_command({"kind": "add", "list": i18n.DEFAULT_LIST, "items": pu["items"]}, now)
        q = i18n.t("purchase_ask", list=self._list_title(i18n.DEFAULT_LIST), items=", ".join(pu["items"]), text=text.strip()[:120])
        self.store.set_conversation(self.owner, {"awaiting": "purchase", "items": pu["items"], "original": text, "question": q,
                                                 "turns": 1, "at": now.isoformat()})
        return [q]

    def _purchase_pick(self, conv: dict, text: str, now: datetime) -> list[str]:
        f = fold(text)
        if re.fullmatch(r"\s*1\s*[).]?\s*", f) or re.search(r"\b(liste\w*|alinacak\w*)\b", f):
            self.store.clear_conversation(self.owner)
            return self._list_command({"kind": "add", "list": i18n.DEFAULT_LIST, "items": conv["items"]}, now)
        if re.fullmatch(r"\s*2\s*[).]?\s*", f) or re.search(r"\b(takvim\w*|etkinlik\w*)\b", f):
            self.store.clear_conversation(self.owner)
            return self._new(conv["original"], now, skip_purchase=True)
        conv["turns"] = conv.get("turns", 1) + 1
        self.store.set_conversation(self.owner, conv)
        return [i18n.t("pick_again")]

    # ------------------------------------------------------------------ reference resolution: move an existing item
    _MOVE_CUE = re.compile(r"\b(at|atal\w*|al|alin\w*|cek\w*|getir\w*|tasi\w*|kaydir\w*|ekle\w*)\b")
    _MOVE_SKIP = re.compile(r"\b(ekle\w*|koy\w*|kaydet\w*|sil\w*|kaldir\w*|iptal\w*|ertele\w*|hazir\w*|hatirlat\w*)\b")
    # 'bugune al/getir/cek/tasi/ekle': a day in the dative plus a move verb moves the named event ('ekle' only here is a move)
    _DAY_MOVE = re.compile(r"\b(?:bugune|yarina|obur gune|ertesi gune|haftaya|pazartesiye|saliya|carsambaya|persembeye|cumaya|cumartesiye|pazara)"
                           r"\s+(?:de\s+|da\s+|lutfen\s+)?(?:ekle\w*|getir\w*|tasi\w*|al|alin\w*|cek\w*|kaydir\w*|at)\b")
    _MOVE_STOP = {"aksam", "sabah", "gece", "ogle", "ogleden", "ikindi", "sonra", "once", "yarin", "yarina", "bugun", "bugune", "yapilmadi",
                  "yapmadim", "olmadi", "cikacak", "cikar", "cikarilacak", "tamam", "olsun", "saat", "gibi", "icin", "biraz", "daha", "gelecek", "getir", "getirin", "tasi", "tasin",
                  "cek", "cekin", "kaydir", "ekle", "alin", "lutfen", "obur", "ertesi", "gune", "haftaya"}

    def _mentions(self, text: str, title: str) -> bool:
        """The text names the item: a content word that starts (or is started by) a word of the title, or a synonym group hit."""
        words = [w for w in readytime.subject_words(text) if w not in self._MOVE_STOP and not w.isdigit()]
        tws = readytime.subject_words(title)
        shared = [w for w in words if any(t.startswith(w) or w.startswith(t) for t in tws if len(t) >= 3)]
        generic = ("toplanti", "randevu", "gorusme", "etkinlik")
        if shared and all(w.startswith(generic) for w in shared) and any(not w.startswith(generic) and w not in shared for w in words if len(w) >= 3):
            return False                  # 'cto ile toplantim' is not the 'Initech toplantim' that shares only the generic word 'toplanti'
        if len(words) >= 5 and len(shared) * 4 < len(words):
            return False                  # a long sentence that shares one word ('kontrol') with a title is about something else
        return readytime.subject_hit(" ".join(words), title) or bool(shared)

    def _move_existing(self, text: str, now: datetime) -> list[str] | None:
        """'cop aksam cikacak 18.30da', 'copu 6ya al', 'cop yapilmadi yarina at': when the text names an existing event of
        today or the next days and carries a new clock time or day, it is an edit preview (before -> after), never a new
        event. Several matches: a short question. Nothing matches: None (the normal create path). Tasks have no time and
        are left alone."""
        f = fold(text)
        day_move = bool(self._DAY_MOVE.search(f))
        if "?" in f or (self._MOVE_SKIP.search(f) and not (day_move and not self._MOVE_SKIP.search(self._DAY_MOVE.sub(" ", f)))):
            return None
        if not self._in_clauses and len(taskmod.split_clauses(text)) >= 2:
            return None                       # a multi-clause message is split first; each clause comes back here
        ft = find_time(text)
        if ft is None and (m := re.search(r"\b(?:aksam|ogleden sonra|ikindi|gece|sabah)\s+(\d{1,2})\b(?![.:])", f)) and int(m[1]) < 24:
            ft = (time(int(m[1]) + (12 if int(m[1]) < 12 and not f[m.start():].startswith("sabah") else 0), 0), [])
        d = find_date(readytime.without_times(text), now.date())
        if d is None and re.search(r"\byarina\b", f):
            d = (now.date() + timedelta(days=1), [])
        if ft is None and not (d and self._MOVE_CUE.search(f)):
            return None
        subject = readytime.without_times(text)
        back = 3 if (d and ft is None) else 0          # a day-only move may name an event that already slipped past
        start = datetime.combine(now.date() - timedelta(days=back), time(0, 0), self.tz)
        try:
            evs = merge(self.hub.events(start, start + timedelta(days=8 + back)))
        except Exception:  # noqa: BLE001
            return None
        hits = [e for e in evs if e.event_id and not e.all_day and e.calendar != "birthdays" and not birthdays.is_birthday(e)
                and self._mentions(subject, e.title)]
        if d and ft is None:
            elsewhere = [e for e in hits if e.start.astimezone(self.tz).date() != d[0]]
            upcoming = [e for e in elsewhere if e.end > now]
            hits = upcoming or elsewhere or hits          # the move candidate: not already on that day, upcoming first, else past
        elif d:
            hits = [e for e in hits if e.start.astimezone(self.tz).date() == d[0]] or hits
        else:
            hits = [e for e in hits if e.start.astimezone(self.tz).date() == now.date()] or hits
        if not hits:
            return None
        hits.sort(key=lambda e: e.start)
        changes: dict = {}
        if ft is not None:
            h, m = ft[0].hour, ft[0].minute
            if h < 12 and not DAYPART.search(f) and not EXPLICIT_CLOCK.search(f):
                day = d[0] if d else now.date()
                later = [x for x in (h, h + 12) if datetime.combine(day, time(x, m), self.tz) > now]
                h = later[0] if later else h
            changes["time"] = f"{h:02d}:{m:02d}"
        if d:
            changes["date"] = d[0].isoformat()
        intent = {"intent": "edit_event", "match": hits[0].title, "date": None, "scope": "this", "changes": changes}
        if len(hits) > 1:
            return self._offer_pick(intent, hits, now)
        out = self._propose_target(intent, hits[0], now)
        return [i18n.t("resched_head", title=hits[0].title) + "\n" + out[0]] + out[1:]

    # ------------------------------------------------------------------ existing event: time and reminder (ADR 0017)
    def _subject_event(self, text: str, now: datetime) -> Event | None:
        """The ONE upcoming timed event the text is about (fuzzy title, the named day or the next days); None when there
        is none or several."""
        d = find_date(readytime.without_times(text), now.date())
        day0 = d[0] if d else now.date()
        start = datetime.combine(day0, time(0, 0), self.tz)
        try:
            evs = merge(self.hub.events(start, start + timedelta(days=1 if d else 8)))
        except Exception:  # noqa: BLE001
            return None
        hits = [e for e in evs if e.event_id and not e.all_day and e.calendar != "birthdays" and not birthdays.is_birthday(e)
                and e.end > now and readytime.subject_hit(text, e.title)]
        if not hits:
            return None
        first = hits[0].start.astimezone(self.tz).date()
        same_day = [e for e in hits if e.start.astimezone(self.tz).date() == first]
        return same_day[0] if len(same_day) == 1 else None

    @staticmethod
    def _clock12(hm: tuple[int, int], ref: int) -> int:
        """Minutes of the day for a bare clock time: the reading (am or pm) closest to the reference minute."""
        base = hm[0] * 60 + hm[1]
        return min([base] + ([base + 720] if hm[0] < 12 else []), key=lambda c: abs(c - ref))

    def _reschedule(self, text: str, now: datetime) -> list[str] | None:
        rt = readytime.parse(text)
        if not rt or not (ev := self._subject_event(text, now)):
            return None
        s = ev.start.astimezone(self.tz)
        cur = s.hour * 60 + s.minute
        appt = self._clock12(rt["appt"], cur) if rt["appt"] else None
        ready = self._clock12(rt["ready"], appt if appt is not None else cur) if rt["ready"] else None
        if ready is not None and ready >= (appt if appt is not None else cur):
            return None
        fmt = lambda m: f"{m // 60:02d}:{m % 60:02d}"                                    # noqa: E731
        read_a: dict = {"intent": "edit_event", "match": ev.title, "date": None, "scope": "this", "changes": {}}
        if appt is not None and appt != cur:
            read_a["changes"]["time"] = fmt(appt)
        if ready is not None:
            read_a["changes"]["reminder_min"] = (appt if appt is not None else cur) - ready
        if not read_a["changes"]:
            return None
        head = i18n.t("resched_head", title=ev.title)
        if rt["leave"] and ready is not None and appt not in (None, cur) and not rt["named"]:
            read_b = {**read_a, "changes": {"reminder_min": cur - ready, "notes": self._leave_note(ev, fmt(appt))}}
            q = i18n.t("resched_ask", title=ev.title, appt=fmt(appt), ready=fmt(ready), cur=fmt(cur), lead_a=i18n.fmt_left(appt - ready),
                       lead_b=i18n.fmt_left(cur - ready))
            self.store.set_conversation(self.owner, {"awaiting": "reading", "options": [read_a, read_b], "event_id": ev.event_id,
                                                     "question": q, "turns": 1, "at": now.isoformat()})
            return [q]
        out = self._propose_target(read_a, ev, now)
        return [head + "\n" + out[0]] + out[1:]

    @staticmethod
    def _leave_note(ev: Event, hhmm: str) -> str:
        note = i18n.t("resched_leave_note", t=hhmm)
        return f"{ev.notes}\n{note}" if ev.notes and note not in ev.notes else note

    def _reading_pick(self, conv: dict, text: str, now: datetime) -> list[str]:
        f = fold(text)
        n = 1 if re.fullmatch(r"\s*1\s*[).]?\s*", f) else 2 if re.fullmatch(r"\s*2\s*[).]?\s*", f) else 0
        if not n:
            conv["turns"] = conv.get("turns", 1) + 1
            self.store.set_conversation(self.owner, conv)
            return [i18n.t("pick_again")]
        self.store.clear_conversation(self.owner)
        ev = self._event_by_id(conv["event_id"])
        if ev is None:
            return [i18n.t("not_found", match=conv["options"][0]["match"])]
        out = self._propose_target(conv["options"][n - 1], ev, now)
        return [i18n.t("resched_head", title=ev.title) + "\n" + out[0]] + out[1:]

    # ------------------------------------------------------------------ tasks (ADR 0013)
    def _find_task(self, match: str):
        return next((r for r in self.store.open_tasks(self.owner) if title_match(match, r["title"])), None)

    def _task_list(self) -> list[str]:
        self._read = True
        rows = self.store.open_tasks(self.owner)
        if not rows:
            return [i18n.t("task_list_empty")]
        self.shown_tasks = True
        return ["\n".join([i18n.t("task_list_head")] + [i18n.t("task_list_row", n=i, x=r["title"]) for i, r in enumerate(rows, 1)])]

    # ------------------------------------------------------------------ per-item tap buttons (ADR 0025)
    def _item_hash(self, item_id: int, text: str) -> str:
        return hashlib.sha256(f"{item_id}:{text}".encode()).hexdigest()[:8]

    def _item_btn(self, act: str, mark: str, item_id: int, text: str) -> dict:
        label = text if len(text) <= ITEM_LABEL_MAX else text[:ITEM_LABEL_MAX - 1] + "…"
        return {"text": i18n.t(mark, x=label), "callback_data": f"{act}:{item_id}:{self._item_hash(item_id, text)}"}

    def _task_keyboard(self) -> dict | None:
        rows = self.store.open_tasks(self.owner)[:ITEM_BUTTONS_MAX]
        if not rows or self.store.open_pending(self.owner):
            return None
        return {"inline_keyboard": [[self._item_btn("t", "btn_item_open", r["id"], r["title"])] for r in rows]}

    def _item_callback(self, action: str, item_id: int, h8: str, now: datetime) -> tuple[str | None, list[str]]:
        """A tap on one item. List item: open <-> done (toggle). Task: open -> done (it leaves the list; closing is one-way).
        Verified against the stored row (owner, id, unchanged text, status); anything else is the stale alert. Own store only: no calendar write."""
        stale = (i18n.t("btn_stale"), [])
        if action == "t":
            row = self.store.get_task(item_id)
            if (row is None or row["chat_id"] != self.owner or row["status"] != "open" or h8 != self._item_hash(item_id, row["title"])):
                return stale
            self.store.close_task(item_id, "done", now)
            self.store.audit("task_tap_done", {"id": item_id}, now, self.owner)
            if not self.store.open_tasks(self.owner):
                return None, [i18n.t("task_tap_all_done", x=row["title"])]
            self.shown_tasks = True
            return None, ["\n".join([i18n.t("task_list_head")] + [i18n.t("task_list_row", n=i, x=r["title"]) for i, r in enumerate(self.store.open_tasks(self.owner), 1)])]
        row = self.store.get_list_item(item_id)
        if (row is None or row["chat_id"] != self.owner or row["status"] not in ("open", "done") or h8 != self._item_hash(item_id, row["text"])
                or row["list_key"] not in i18n.LISTS):
            return stale
        self.store.set_list_item_status(item_id, "done" if row["status"] == "open" else "open", now)
        self.store.audit("list_item_tap", {"list": row["list_key"], "to": "done" if row["status"] == "open" else "open"}, now, self.owner)
        return None, [self._list_view(row["list_key"], now)]

    NUMREF = re.compile(r"^(\d{1,2})(?:\.|\)|\s*(?:i|u|yi|nci|inci|ncu|numarali|nolu)\b)?\s*(?:gorev\w*\s+)?(.+)$")
    NUMREF_ACTION = re.compile(r"\b(yapildi|yaptim|tamam\w*|bitti|bitirdim|halloldu|hallettim|sil\w*|kaldir\w*|iptal|ertele\w*|yarina|birak\w*|at)\b")

    def _task_number_ref(self, text: str) -> str | None:
        """'2 yapildi', '1 i yarina ertele', '3. gorev tamam' right after the bot showed a numbered task list: the text with the
        number replaced by that task's title, so the ordinary task statements (done / defer / remove) handle it. None otherwise."""
        m = self.NUMREF.match(fold(text).strip(" .!?,;"))
        if not m or not self.NUMREF_ACTION.search(m[2]) or self.store.open_pending(self.owner) or self.store.get_conversation(self.owner):
            return None
        outs = [r["text"] for r in self.store.recent_messages(self.owner, 6) if r["direction"] == "out"]
        if not outs or i18n.t("task_list_head") not in outs[-1]:
            return None
        row = re.search(rf"^{int(m[1])}\. (.+)$", outs[-1], re.M)
        if not row or not any(r["title"] == row[1] for r in self.store.open_tasks(self.owner)):
            return None
        return f"{row[1]} {m[2]}"

    def _remaining(self, now: datetime) -> list[str]:
        """'bugun yapilmasi gereken neler kaldi?': today's events that have not ended yet, then the open tasks. Never the whole day."""
        self._read = True
        d0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            finished = self.store.done_event_ids(self.owner)
            evs = [e for e in merge(self.hub.events(d0, d0 + timedelta(days=1))) if (e.end > now or e.all_day) and e.event_id not in finished]
        except Exception:  # noqa: BLE001
            return [i18n.t("calendar_down")]
        lines = self._day_lines(evs, d0)
        rows = self.store.open_tasks(self.owner)
        parts = []
        if lines:
            parts.append("\n".join([i18n.t("remaining_head", date=i18n.fmt_date(d0))] + lines))
        if rows:
            parts.append("\n".join([i18n.t("task_list_head")] + [i18n.t("task_list_row", n=i, x=r["title"]) for i, r in enumerate(rows, 1)]))
        return ["\n\n".join(parts)] if parts else [i18n.t("remaining_none")]

    def _task_bulk(self, text: str, now: datetime) -> list[str] | None:
        """'cop acme duruyor digerlerini done yap', 'cop aksam 6dan sonra', 'saat 6dan sonra saat basi hatirlat': statements
        about the open tasks as a whole. They leave any open question or preview alone."""
        rows = self.store.open_tasks(self.owner)
        titles = [r["title"] for r in rows]
        if (nag := taskmod.parse_nag(text)):
            h, m = nag["at"]
            self.store.set_meta("task_nag", json.dumps({"date": now.date().isoformat(), "at": f"{h:02d}:{m:02d}"}))
            self._acted = True
            return [i18n.t("task_nag_on", t=f"{h:02d}:{m:02d}", n=len(rows))]
        if (bd := taskmod.parse_bulk_done(text, titles)) is not None:
            keep = set(bd["keep"])
            closed = [r for i, r in enumerate(rows) if i not in keep]
            if not closed:
                return [i18n.t("task_none")]
            kept = [r["title"] for i, r in enumerate(rows) if i in keep]
            note = i18n.t("task_bulk_kept", kept=", ".join(kept)) if kept else ""
            return [x + (("\n" + note) if note else "") for x in self._propose("task_write", self._task_write_args("done", closed), [], now)]
        if (tm := taskmod.parse_tomorrow(text)):
            return self._task(tm, now)
        if (groups := taskmod.parse_after_groups(text)):
            return self._after_evening(groups, rows, now)
        return None

    def _after_evening(self, groups: list[dict], rows, now: datetime) -> list[str] | None:
        """'cop ve acme aksam 6dan sonra ...': the named open tasks get that hour, and today's calendar events of those names
        are MOVED into free slots from that hour on (a preview: calendar changes need 'evet'). Names that match nothing are said."""
        after = json.loads(self.store.get_meta("task_after", "{}") or "{}")
        today = self._today_events(now)
        task_lines, picked, unknown = [], {}, []
        for g in groups:
            h, m = g["at"]
            hit_t = [r for r in rows if taskmod.words_match(g["words"], r["title"])]
            hit_e = [e for e in today if taskmod.words_match(g["words"], e.title)]
            if not hit_t and not hit_e:
                unknown.append(" ".join(g["words"]))
                continue
            for r in hit_t:
                after[str(r["id"])] = f"{h:02d}:{m:02d}"
                task_lines.append(i18n.t("task_after_row", x=r["title"], t=f"{h:02d}:{m:02d}"))
            for e in hit_e:
                picked.setdefault(e.event_id, (e, h * 60 + m))
        if not task_lines and not picked:
            return None
        if task_lines:
            self._acted = True
            self.store.set_meta("task_after", json.dumps(after))
        parts = [i18n.t("task_after_head") + "\n" + "\n".join(task_lines)] if task_lines else []
        if picked:
            moves, nofit = self._evening_plan(list(picked.values()), today)
            if moves:
                parts.append(self._propose("edit_events", {"moves": moves}, [], now)[0])
            if nofit:
                parts.append(i18n.t("evening_nofit", x=", ".join(nofit)))
        if unknown:
            parts.append(i18n.t("evening_unknown", x="; ".join(f"'{u}'" for u in unknown)))
        return ["\n\n".join(parts)]

    def _today_events(self, now: datetime) -> list[Event]:
        d0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            return [e for e in merge(self.hub.events(d0, d0 + timedelta(days=1))) if not e.all_day and not birthdays.is_birthday(e)
                    and e.event_id and e.start.astimezone(self.tz).date() == now.date()]
        except Exception:  # noqa: BLE001
            return []

    def _evening_plan(self, picked: list[tuple[Event, int]], today: list[Event]) -> tuple[list[dict], list[str]]:
        """Sequential free slots (5-minute grid) from each event's hour on; events already at or after it stay put."""
        ids = {e.event_id for e, _ in picked}
        mins = lambda dt: dt.astimezone(self.tz).hour * 60 + dt.astimezone(self.tz).minute          # noqa: E731
        busy = [(mins(e.start), mins(e.end)) for e in today if e.event_id not in ids]
        moves, nofit, cursor = [], [], 0
        for e, at in sorted(picked, key=lambda x: x[0].start):
            cur = mins(e.start)
            if cur >= at:
                continue
            dur = max(5, int((e.end - e.start).total_seconds() // 60))
            t = -(-max(at, cursor) // 5) * 5
            while t + dur <= 24 * 60 and any(t < b1 and b0 < t + dur for b0, b1 in busy):
                t += 5
            if t + dur > 24 * 60:
                nofit.append(e.title)
                continue
            busy.append((t, t + dur))
            cursor = t + dur
            day = e.start.astimezone(self.tz).date()
            ns = datetime.combine(day, time(t // 60, t % 60), self.tz)
            moves.append({"event_id": e.event_id, "title": e.title, "before": f"{cur // 60:02d}:{cur % 60:02d}",
                          "start": ns.isoformat(), "end": (ns + timedelta(minutes=dur)).isoformat()})
        return moves, nofit

    def _events_soon(self, match: str, now: datetime) -> list[Event]:
        """Calendar events of today or tomorrow that look like `match` (a finished task may have one)."""
        days = {now.date(), now.date() + timedelta(days=1)}
        try:
            return [e for e in self.find_events(match, None, now) if e.start.astimezone(self.tz).date() in days]
        except Exception:  # noqa: BLE001
            return []

    def _task(self, tk: dict, now: datetime) -> list[str] | None:
        """Tasks live in Sebastian's own store. Finishing one never touches the calendar: a matching event of today or
        tomorrow is only OFFERED for removal through the normal pending delete preview ('evet sil')."""
        kind, title = tk["kind"], tk.get("title", "")
        if kind == "list":
            return self._task_list()
        row = self._find_task(title) if title else None
        if kind == "add":
            if row and (fold(row["title"]) == fold(title) or tk.get("implicit")):
                return [i18n.t("task_dup", x=row["title"])]
            due = (now.date() + timedelta(days=1)).isoformat() if tk.get("due") == "tomorrow" else None
            if due and any(fold(r["title"]) == fold(title) and r["due"] == due for r in self.store.open_tasks(self.owner)):
                return [i18n.t("task_dup", x=title)]
            if tk.get("implicit") and taskmod.is_meta(title):
                return [i18n.t("meta_ask")]       # an instruction about the list is never stored as a task title
            self.store.add_task(self.owner, title, now, due)
            self._acted = True
            if due:
                self.store.clear_conversation(self.owner)      # a stale 'saat kacta?' about this same wish is moot
            return [i18n.t("task_added_tomorrow" if due else "task_added", x=title)]
        if kind == "remove":
            if not row:
                return [i18n.t("task_not_found", x=title)]
            return self._propose("task_write", self._task_write_args("removed", [row]), [], now)
        events = self._events_soon(title, now)
        if not row and not events:
            return [i18n.t("task_none")] if tk["strong"] else None
        out = []
        if row:
            self.store.close_task(row["id"], "done", now)
            self._acted = True
            out.append(i18n.t("task_done", x=row["title"]))
        for e in self._finished_events(events, now):          # a finished event is closed here, never deleted from the calendar
            self.store.mark_event_done(self.owner, e.event_id, e.title, now)
            self._acted = True
            out.append(i18n.t("st_done_event", x=e.title))
        return out or [i18n.t("task_none")]

    # ------------------------------------------------------------------ ask-until-sure
    def _reask(self, conv: dict) -> str:
        aw = conv.get("awaiting")
        if aw == "pick":
            return i18n.t("pick_again")
        return i18n.ask({"title": "ask_title", "date": "ask_date", "time": "ask_time"}.get(aw, "")) if aw in ("title", "date", "time") \
            else i18n.t("open_question")

    def _ask_free(self, q: str, text: str, now: datetime) -> list[str]:
        """The ONE way a free-text question from the model reaches the owner (invariant I10). A yes/no offer is only sent
        with a pending action behind it: the rules turn the owner's own words into the preview; when they cannot, it is
        rephrased as an open question. Any question that is sent has a clarification state, so 'evet' is never 'nothing
        pending'."""
        if re.search(i18n.YES_NO_QUESTION, fold(q)):
            alt = rules.parse(text, now.date())
            if alt["intent"] in ("add_event", "edit_event", "delete_event", "list_events", "birthdays", "help", "chitchat"):
                return self._dispatch(alt, text, now)
            if (g := self._best_guess(text, now)) is not None:
                return g                              # the owner's words, read as well as possible, not a bare question
            q = i18n.t("open_question")
        self.store.set_conversation(self.owner, {"awaiting": "clarify", "question": q, "original": text, "turns": 1,
                                                 "at": now.isoformat()})
        return [q]

    def _answer(self, conv: dict, text: str, now: datetime) -> list[str]:
        turns = conv.get("turns", 1) + 1
        aw = conv["awaiting"]
        if turns > MAX_TURNS:
            self.store.clear_conversation(self.owner)
            return [i18n.t("gave_up")]
        if aw == "pick":
            return self._pick(conv, text, now)
        if aw == "reading":
            return self._reading_pick(conv, text, now)
        if aw == "defer_when":
            return self._defer_when(conv, text, now)
        if aw == "purchase":
            return self._purchase_pick(conv, text, now)
        if aw == "daypick":
            return self._daypick(conv, text, now, turns)
        if aw == "clarify":
            intent, serr = self._classify(text, now, {"question": conv["question"], "partial": {"original": conv["original"]}},
                                          conv["original"])
            if intent is None:
                intent = rules.parse(f"{conv['original']} {text}", now.date())
            self.store.clear_conversation(self.owner)
            if intent["intent"] == "unclear":
                return [i18n.t("unclear_examples")]
            return self._dispatch(intent, text, now)
        draft = dict(conv["draft"])
        f = fold(text)
        if aw == "ampm":
            h, m = map(int, draft["time"].split(":"))
            tm = find_time(text)
            if re.fullmatch(r"\s*1\s*[).]?\s*", f) or re.search(r"\bsabah\b", f) or (tm and tm[0] == time(h, m)):
                draft["time"] = f"{h:02d}:{m:02d}"
            elif re.fullmatch(r"\s*2\s*[).]?\s*", f) or re.search(r"\b(aksam|ogleden|ikindi|gece)\b", f) or (tm and tm[0] == time(h + 12, m)):
                draft["time"] = f"{h + 12}:{m:02d}"
            else:
                conv["turns"] = turns
                self.store.set_conversation(self.owner, conv)
                return [i18n.t("pick_again_ampm")]
            draft["_ampm"] = True
            return self._add(draft, text, now, turns)
        if aw == "title":
            draft["title"] = clean(text, nlmod.MAX_TITLE) or None
        got: list[str] = []
        if aw == "date":
            d = find_date(text, now.date())
            if not d and (m := re.fullmatch(r"\s*([123])\s*[).]?\s*", f)):      # the numbered choices of the day question
                n = int(m[1])
                if n == 3 or re.search(r"\bbaska\b", f):
                    q = i18n.t("ask_date_other")
                    self.store.set_conversation(self.owner, {"awaiting": "date", "draft": draft, "turns": turns,
                                                             "at": now.isoformat(), "question": q})
                    return [q]
                d = (now.date() + timedelta(days=n - 1), [])
            elif not d and re.search(r"\bbaska gun\b", f):
                q = i18n.t("ask_date_other")
                self.store.set_conversation(self.owner, {"awaiting": "date", "draft": draft, "turns": turns,
                                                         "at": now.isoformat(), "question": q})
                return [q]
            if d:
                draft["date"] = d[0].isoformat()
                draft["_orig"] = f"{draft.get('_orig', '')} {text}".strip()
                got.append("date")
        elif aw == "time":
            if re.search(r"\b(tum gun|gun boyu)\b", f):
                draft["all_day"] = True
            elif (fz := fuzzytime.find(text)) and (fz.start or fz.duration):
                self._fuzzy_fill(draft, text)
                if fz.start:
                    draft["_ans_time"] = text
                    got.append("time")
            else:
                tm = find_time(text)
                if tm is None and (m := re.fullmatch(r"\s*(\d{1,2})\s*", text)) and int(m[1]) < 24:
                    tm = (time(int(m[1]), 0), [])
                if tm:
                    draft["time"] = tm[0].strftime("%H:%M")
                    draft["_ans_time"] = text
                    got.append("time")
            if not draft.get("date") and (d := find_date(text, now.date())):
                draft["date"] = d[0].isoformat()
                got.append("date")
        if aw in ("date", "time") and not draft.get("all_day"):      # an answer may carry the other field too: read it
            if not draft.get("time") and (tm := find_time(text)):
                draft["time"], draft["_ans_time"] = tm[0].strftime("%H:%M"), text
                got.append("time")
            if not draft.get("date") and (d := find_date(text, now.date())):
                draft["date"] = d[0].isoformat()
                got.append("date")
        lead = ""
        if dialogue.is_restating(text):                              # ADR 0014: never repeat the question, say what was understood
            if "time" in got:
                lead = i18n.t("sorry_time")
            elif "date" in got:
                lead = i18n.t("sorry_date")
            else:
                lead = i18n.t("restate", x=", ".join(x for x in (draft.get("title"), draft.get("time")) if x))
        if lead:
            self._restated = True
            out = self._add(draft, text, now, turns)
            return [lead + "\n" + out[0]] + out[1:]
        # a model may help merge, but code decides: only the fields that were missing are filled above
        return self._add(draft, text, now, turns)

    def _add(self, it: dict, text: str, now: datetime, turns: int) -> list[str]:
        """Ask for the next missing field, else store a pending add and show the preview."""
        def ask(aw: str, key: str, question: str | None = None) -> list[str]:
            prev = self.store.get_conversation(self.owner) or {}
            n = prev.get("ask_n", 0) + 1 if prev.get("awaiting") == aw else 0      # same question again: reword it
            conv = {"awaiting": aw, "draft": it, "turns": turns, "at": now.isoformat(), "ask_n": n}
            if question:
                conv["question"] = question
            self.store.set_conversation(self.owner, conv)
            return [question or i18n.ask(key, n)]
        if not it.get("title"):
            return ask("title", "ask_title")
        self._fuzzy_fill(it, text)
        if not it.get("date"):
            if it.get("time") and not it.get("all_day"):          # smart default (ADR 0014): the next upcoming occurrence
                q = self._infer_date(it, text, now)
                if q:
                    return ask("ampm", "", q)
            elif DAYPART.search(fold(f"{text} {it.get('_orig', '')}")) and not it.get("all_day"):
                return ask("time", "ask_time")                      # a day part alone: the hour decides the day
            else:
                return ask("date", "ask_date")
        if not it.get("all_day") and not it.get("time"):
            if not it.get("duration_min") and self._is_chore(f"{it['title']} {it.get('_orig', '')}"):
                it["all_day"], it["_allday_default"] = True, True     # a to-do on a named day: all-day, said in the preview (ADR 0015)
            else:
                return ask("time", "ask_time")
        if (opts := self._day_options(it, now)):
            q = i18n.t("ask_day", wd=i18n.DAYS[now.weekday()], d0=i18n.fmt_day_month(date.fromisoformat(opts[0])),
                       d1=i18n.fmt_day_month(date.fromisoformat(opts[1])))
            self.store.set_conversation(self.owner, {"awaiting": "daypick", "draft": it, "options": opts, "question": q,
                                                     "turns": turns, "at": now.isoformat()})
            return [q]
        if it.get("time") and not it.get("all_day"):
            h, m = map(int, it["time"].split(":"))
            src = f"{text} {it.get('_ans_time', '')} {it.get('_src', '')}"
            if 1 <= h <= 6 and not it.get("_ampm") and not DAYPART.search(fold(src)) and not EXPLICIT_CLOCK.search(src):
                it["_src"] = (it.get("_src", "") + " " + text).strip()
                it["time"] = None
                return ask("time", "", i18n.t("ask_time_ambig", t=f"{h:02d}:{m:02d}", pm=f"{h + 12}:{m:02d}"))
        draft = self._build_draft(it)
        self.store.clear_conversation(self.owner)
        if (dup := self._duplicate(draft, now)) is not None:
            return self._already(dup, f"{text} {it.get('_orig', '')}", it.get("reminder_min"), now)
        notes = []
        if it.get("_assumed") and it.get("time"):
            notes.append(i18n.t("p_assumed", t=it["time"]))
        if it.get("_fuzzy") and not it.get("all_day"):
            notes.append(i18n.t("p_fuzzy", t=it["_fuzzy"]["t"], said=it["_fuzzy"]["said"]))
        if it.get("_allday_default"):
            notes.append(i18n.t("p_allday_default"))
        extra: dict = {}
        if (near := self._near_duplicate(draft, now)) is not None:
            extra["near_dup"] = {"event_id": near.event_id, "title": near.title}
            notes.append(i18n.t("p_near_dup", x=near.title, t=f"{near.start.astimezone(self.tz):%H:%M}"))
        note = "\n".join(notes) or None
        return self._propose("add_event", {"drafts": [draft_to_dict(draft)], **extra, **({"keep_task": True} if it.get("_keep_task") else {})}, [draft], now, note=note)

    def _fuzzy_fill(self, it: dict, text: str) -> None:
        """A fuzzy time or range ('ogleden sonra 4-5 gibi', 'aksama dogru') the owner said but nothing has read yet, and a
        stated duration ('yarim saat') (ADR 0015). Only fills what is missing."""
        if it.get("all_day") or it.get("time"):
            return
        fz = fuzzytime.find(f"{it.get('_orig', '')} {text}")
        if fz is None:
            return
        if fz.duration and not it.get("duration_min"):
            it["duration_min"] = fz.duration
        if fz.start:
            it.update({"time": fz.start.strftime("%H:%M"), "_ampm": True, "_fuzzy": {"t": fuzzytime.clock(fz), "said": fz.said},
                       "_next_day": fz.next_day})
            if fz.end:
                it["end_time"] = fz.end.strftime("%H:%M")

    @staticmethod
    def _is_chore(title: str) -> bool:
        """A to-do style item (duzenle, topla, temizle, al, yap, cikar, ...) and not an appointment (ADR 0015)."""
        words = title_words(title)
        if any(w.startswith(i18n.APPOINTMENT_STEMS) for w in words):
            return False
        return any(w.startswith(i18n.TODO_STEMS + i18n.CHORE_STEMS) or w in i18n.TODO_WORDS for w in words)

    # ------------------------------------------------------------------ already there (ADR 0015)
    def _duplicate(self, d: EventDraft, now: datetime) -> Event | None:
        """An existing event of the same day and start (or both all-day) whose title is about the same thing."""
        day = d.start.astimezone(self.tz).replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            evs = merge(self.hub.events(day, day + timedelta(days=1)))
        except Exception:  # noqa: BLE001
            return None
        for e in evs:
            if e.calendar == "birthdays" or not e.event_id or birthdays.is_birthday(e) or e.all_day != d.all_day:
                continue
            if (d.all_day or e.start == d.start) and similar_title(d.title, e.title):
                return e
        return None

    def _near_duplicate(self, d: EventDraft, now: datetime) -> Event | None:
        """An upcoming, single (not recurring) timed event of the same day about the same thing (at least two shared words) at a
        DIFFERENT time: the add preview mentions it and offers to move it instead (principle: resolve references to existing items)."""
        if d.all_day:
            return None
        day = d.start.astimezone(self.tz).replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            evs = merge(self.hub.events(day, day + timedelta(days=1)))
        except Exception:  # noqa: BLE001
            return None
        mine = {w for w in title_words(d.title) if len(w) > 2}
        for e in evs:
            if e.calendar == "birthdays" or not e.event_id or birthdays.is_birthday(e) or e.all_day or e.series_id or e.end <= now or e.start == d.start:
                continue
            theirs = {w for w in title_words(e.title) if len(w) > 2}
            shared = [w for w in mine if any(same_stem(w, v) for v in theirs)]
            if len(shared) >= 2 and len(shared) >= min(len(mine), len(theirs)) - 1 and similar_title(d.title, e.title):
                return e
        return None

    def _already_text(self, e: Event) -> str:
        line = i18n.t("already_there", x=f"{e.title}, {self._when(e.start, e.end, e.all_day, rel=True)}")
        if e.all_day or e.tag == "birthday":
            return line + (" " + i18n.t("already_reminder_allday") if e.all_day else "")
        lead = e.reminder_minutes if e.reminder_minutes is not None else self.default_lead
        return line + " " + i18n.t("already_reminder", x=i18n.fmt_left(lead))

    def _already(self, e: Event, said: str, want_min: int | None, now: datetime) -> list[str]:
        """The owner asked for something that is already in the calendar: say so, write nothing. A reminder wish becomes
        a real reminder-change preview (the stated lead, or one hour before)."""
        head = self._already_text(e)
        self.store.set_meta("last_item", json.dumps({"title": e.title, "at": now.isoformat()}))
        if e.all_day or not (want_min is not None or re.search(r"hatirlat", fold(said))):
            return [head]
        cur = e.reminder_minutes if e.reminder_minutes is not None else self.default_lead
        mins = want_min if want_min is not None else (60 if cur != 60 else 120)
        if mins == cur:
            return [head]
        offer = self._propose_target({"intent": "edit_event", "match": e.title, "date": None, "scope": "this",
                                      "changes": {"reminder_min": mins}}, e, now)
        offer[0] = offer[0].replace(i18n.t("preview_edit"), i18n.t("reminder_offer", x=i18n.fmt_left(mins)), 1)
        return [head] + offer

    def _same_state(self, e: Event, ch: dict) -> bool:
        """True when applying `ch` to `e` would change nothing (so nothing may be previewed or written)."""
        for k, v in ch.items():
            if k in ("start", "end"):
                v = _dt(v) if isinstance(v, str) else v
            cur = {"title": e.title, "location": e.location, "notes": e.notes, "all_day": e.all_day, "start": e.start, "end": e.end,
                   "reminder_minutes": e.reminder_minutes if e.reminder_minutes is not None else self.default_lead}.get(k, object())
            if v != cur:
                return False
        return True

    def _event_by_id(self, event_id: str) -> Event | None:
        now = self.clock()
        try:
            return next((e for e in self.hub.events(now - timedelta(days=2), now + timedelta(days=125)) if e.event_id == event_id), None)
        except Exception:  # noqa: BLE001
            return None

    def _next_occurrence(self, hhmm: str, now: datetime) -> date:
        h, m = map(int, hhmm.split(":"))
        today = now.date()
        return today if datetime.combine(today, time(h, m), self.tz) > now else today + timedelta(days=1)

    def _infer_date(self, it: dict, text: str, now: datetime) -> str | None:
        """An add with a time and no date takes the next upcoming occurrence of that time (ADR 0014) and is marked
        `_assumed` so the preview says so. Returns a question only when an hour 1-6 without a day part is genuinely
        50/50 (both the AM and the PM reading lie ahead, or both are tomorrow): ONE question with two numbered choices."""
        h, m = map(int, it["time"].split(":"))
        src = f"{text} {it.get('_ans_time', '')} {it.get('_src', '')} {it.get('_orig', '')}"
        if 1 <= h <= 6 and not it.get("_ampm") and not DAYPART.search(fold(src)):
            today = now.date()
            am_ahead = datetime.combine(today, time(h, m), self.tz) > now
            pm_ahead = datetime.combine(today, time(h + 12, m), self.tz) > now
            if am_ahead == pm_ahead:
                day = i18n.DAY_WORDS["today" if am_ahead else "tomorrow"]
                return i18n.t("ask_ampm", a=f"{h:02d}:{m:02d}", b=f"{h + 12}:{m:02d}", day=day)
            it["time"] = f"{h + 12}:{m:02d}"
        it["date"] = self._next_occurrence(it["time"], now).isoformat()
        it["_assumed"] = True
        return None

    def _day_options(self, it: dict, now: datetime) -> list[str] | None:
        """[today, today + 7 days] when the owner named today's weekday bare ('persembe' on a Thursday) and the date is one
        of the two (ADR 0012); None when there is nothing to ask. A time that has already passed today means next week."""
        today = now.date()
        if it.get("_dayok") or not names_today_weekday(it.get("_orig", ""), today):
            return None
        nxt = today + timedelta(days=7)
        if it["date"] not in (today.isoformat(), nxt.isoformat()):
            return None
        if it.get("time") and not it.get("all_day"):
            h, m = map(int, it["time"].split(":"))
            if datetime.combine(today, time(h, m), self.tz) <= now:
                return None
        return [today.isoformat(), nxt.isoformat()]

    def _daypick(self, conv: dict, text: str, now: datetime, turns: int) -> list[str]:
        f = fold(text)
        opts = conv["options"]
        if re.search(r"\bhafta\w*|\bgelecek\b|\bsonraki\b|\bikinci\b|\b2\b|\biki\b", f):
            pick = opts[1]
        elif re.search(r"\bbugun\b|\bbu gun\b|\bilk\w*|\b1\b|\bbir\b|\bbirinci\b", f):
            pick = opts[0]
        elif (d := find_date(text, now.date())) and d[0].isoformat() in opts:
            pick = d[0].isoformat()
        else:
            conv["turns"] = turns
            self.store.set_conversation(self.owner, conv)
            return [i18n.t("pick_again_day")]
        draft = dict(conv["draft"])
        draft["date"], draft["_dayok"] = pick, True
        return self._add(draft, text, now, turns)

    @staticmethod
    def _default_duration(title: str) -> int:
        """15 minutes for a chore or short task, 60 for everything else (an appointment), unless the owner said (ADR 0014)."""
        return default_duration(title)

    def _build_draft(self, it: dict) -> EventDraft:
        d = date.fromisoformat(it["date"])
        if it.get("_next_day") and not it.get("_assumed") and not it.get("all_day"):
            d += timedelta(days=1)                      # 'gece yarisi' of the named day is 00:00 of the next one
        if it.get("all_day"):
            start = datetime.combine(d, time(0, 0), self.tz)
            end = start + timedelta(days=1)
        else:
            h, m = map(int, it["time"].split(":"))
            start = datetime.combine(d, time(h, m), self.tz)
            end = start + timedelta(minutes=it.get("duration_min") or self._default_duration(it["title"]))
            if it.get("end_time"):
                eh, em = map(int, it["end_time"].split(":"))
                e2 = datetime.combine(d, time(eh, em), self.tz)
                if e2 > start:
                    end = e2
        return EventDraft(it["title"], start, end, bool(it.get("all_day")), it.get("location") or "", it.get("notes") or "",
                          it.get("reminder_min"), it.get("recurrence") or "", it.get("tag") or "")

    # ------------------------------------------------------------------ edit / delete targets
    def find_events(self, match: str, on: str | None, now: datetime) -> list[Event]:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        evs = [e for e in merge(self.hub.events(start, start + timedelta(days=120))) if e.calendar != "birthdays" and e.event_id]
        if on:
            d = date.fromisoformat(on)
            evs = [e for e in evs if e.start.astimezone(self.tz).date() == d]
        scored = [(e.start, e) for e in evs if title_match(match, e.title)]
        return [e for _, e in sorted(scored, key=lambda x: x[0])]

    def _target(self, intent: dict, now: datetime, text: str = "") -> list[str]:
        today = now.date()
        both = text and intent.get("date") in (today.isoformat(), (today + timedelta(days=7)).isoformat()) \
            and names_today_weekday(text, today)
        try:
            if both:           # 'persembe' on a Thursday: today or next week, look at both
                days = {today.isoformat(), (today + timedelta(days=7)).isoformat()}
                found = [e for e in self.find_events(intent["match"], None, now)
                         if e.start.astimezone(self.tz).date().isoformat() in days]
            else:
                found = self.find_events(intent["match"], intent.get("date"), now)
        except Exception:  # noqa: BLE001
            return [i18n.t("calendar_down")]
        if not found:
            if intent["intent"] == "delete_event" and (row := self._find_task(intent["match"])):
                return self._propose("task_write", self._task_write_args("removed", [row]), [], now)       # no such event, but a task of that name: preview dropping the task
            return [i18n.t("not_found", match=intent["match"])]
        if len(found) > 1:
            return self._offer_pick(intent, found, now)
        return self._propose_target(intent, found[0], now)

    def _offer_pick(self, intent: dict, found: list[Event], now: datetime) -> list[str]:
        cands = found[:8]
        self.store.set_conversation(self.owner, {"awaiting": "pick", "intent": intent, "turns": 1, "at": now.isoformat(),
                                                 "cands": [e.event_id for e in cands]})
        lines = [i18n.t("pick_line", n=i + 1, when=self._when_short(e), title=e.title) for i, e in enumerate(cands)]
        return [i18n.t("ask_pick") + "\n" + "\n".join(lines)]

    def _pick(self, conv: dict, text: str, now: datetime) -> list[str]:
        f = fold(text)
        n = int(m[1]) if (m := re.search(r"\b(\d{1,2})\b", f)) else (1 if "ilk" in f else None)
        if n is None or not 1 <= n <= len(conv["cands"]):
            conv["turns"] = conv.get("turns", 1) + 1
            self.store.set_conversation(self.owner, conv)
            return [i18n.t("pick_again")]
        eid = conv["cands"][n - 1]
        found = [e for e in self.hub.events(now - timedelta(days=2), now + timedelta(days=125)) if e.event_id == eid]
        self.store.clear_conversation(self.owner)
        if not found:
            return [i18n.t("not_found", match=conv["intent"]["match"])]
        return self._propose_target(conv["intent"], found[0], now)

    def _when_short(self, e: Event) -> str:
        s = e.start.astimezone(self.tz)
        return f"{i18n.fmt_date(s, True)} " + (i18n.t("all_day") if e.all_day else f"{s:%H:%M}")

    def _propose_target(self, intent: dict, e: Event, now: datetime) -> list[str]:
        scope = intent.get("scope", "this")
        target = e.series_id if (scope == "all" and e.series_id) else e.event_id
        if intent["intent"] == "delete_event":
            args = {"event_id": target, "title": e.title, "when": self._when(e.start, e.end, e.all_day), "scope": scope if e.series_id else "this"}
            if e.series_id:
                args["recurring"] = True
            return self._propose("delete_event", args, [e], now)
        ch, out = intent["changes"], {}
        s, en = e.start.astimezone(self.tz), e.end.astimezone(self.tz)
        if ("date" in ch or "time" in ch) and scope == "all" and e.series_id:
            return [i18n.t("series_move")]
        if "time" in ch and e.all_day:
            return [i18n.t("no_change_allday")]
        if "title" in ch:
            out["title"] = ch["title"]
        if "location" in ch:
            out["location"] = ch["location"]
        if "notes" in ch:
            out["notes"] = ch["notes"]
        if "reminder_min" in ch:
            out["reminder_minutes"] = ch["reminder_min"]
        if "date" in ch or "time" in ch or "duration_min" in ch:
            nd = date.fromisoformat(ch["date"]) if "date" in ch else s.date()
            if e.all_day:
                ns = datetime.combine(nd, time(0, 0), self.tz)
                ne = ns + (en - s)
            else:
                h, m = map(int, ch["time"].split(":")) if "time" in ch else (s.hour, s.minute)
                ns = datetime.combine(nd, time(h, m), self.tz)
                ne = ns + (timedelta(minutes=ch["duration_min"]) if "duration_min" in ch else en - s)
            out["start"], out["end"], out["all_day"] = ns.isoformat(), ne.isoformat(), e.all_day
        if self._same_state(e, out):                  # never a preview with identical before and after (ADR 0015)
            return [self._already_text(e)]
        before = self._when(e.start, e.end, e.all_day)
        if set(out) == {"reminder_minutes"}:
            cur = e.reminder_minutes if e.reminder_minutes is not None else self.default_lead
            before += ", " + i18n.t("p_reminder", x=i18n.fmt_left(cur))
        args = {"event_id": target, "title": e.title, "before": before, "changes": out,
                "scope": scope if e.series_id else "this"}
        return self._propose("edit_event", args, [e], now)

    # ------------------------------------------------------------------ preview + gate
    def _when(self, start: datetime, end: datetime, all_day: bool, rel: bool = False) -> str:
        s, e = start.astimezone(self.tz), end.astimezone(self.tz)
        day = i18n.fmt_date_full(s)
        if rel and (lead := {0: i18n.DAY_WORDS["today"], 1: i18n.DAY_WORDS["tomorrow"]}.get((s.date() - self.clock().date()).days)):
            day = f"{lead} {day}"
        if all_day:
            return i18n.t("ev_when_allday", date=day)
        return i18n.t("ev_when_timed", date=day, start=f"{s:%H:%M}", end=f"{e:%H:%M}")

    def _conflicts(self, d: EventDraft) -> list[Event]:
        if d.all_day:
            return []
        try:
            return [e for e in merge(self.hub.events(d.start, d.end)) if not e.all_day and not birthdays.is_birthday(e)
                    and e.start < d.end and e.end > d.start]
        except Exception:  # noqa: BLE001
            return []

    def _draft_lines(self, d: EventDraft) -> list[str]:
        lines = [d.title, self._when(d.start, d.end, d.all_day, rel=True)]
        if d.location:
            lines.append(i18n.t("p_location", x=d.location))
        if d.notes:
            lines.append(i18n.t("p_notes", x=d.notes))
        if not d.all_day and d.tag != "birthday":
            if d.reminder_minutes is not None:
                lines.append(i18n.t("p_reminder", x=i18n.fmt_left(d.reminder_minutes)) if d.reminder_minutes else i18n.t("p_reminder_exact"))
            else:
                lines.append(i18n.t("p_reminder_default", x=i18n.fmt_left(self.default_lead)))
        if d.recurrence:
            lines.append(i18n.t("p_recurs_" + d.recurrence))
        for c in self._conflicts(d)[:2]:
            lines.append(i18n.t("p_conflict", title=c.title, when=f"{c.start.astimezone(self.tz):%H:%M}-{c.end.astimezone(self.tz):%H:%M}"))
        return lines

    def _propose(self, intent: str, args: dict, objs: list, now: datetime, ttl: timedelta | None = None,
                 note: str | None = None) -> list[str]:
        pid = self.store.add_pending(self.owner, intent, args, now, ttl) if ttl else self.store.add_pending(self.owner, intent, args, now)
        self.store.audit("pending", {"id": pid, "intent": intent}, now, self.owner)
        self.shown = pid
        text = self._render(intent, args, objs, note)
        self.note_preview(pid, text)
        return [text]

    def note_preview(self, pid: int, text: str) -> None:
        """Remember the code-rendered preview of a pending version: agent mode mints a grant only when the last bot
        message contains exactly this text (ADR 0019)."""
        row = self.store.get_pending(pid)
        if row is not None:
            self.store.set_meta("shown_preview", json.dumps({"pid": pid, "hash": row["args_hash"], "text": text}, ensure_ascii=False))

    def render_pending(self, row) -> str:
        """The code-rendered preview of a stored pending action (the same text `_propose` showed)."""
        return self._render(row["intent"], json.loads(row["args_json"]), [], None)

    def _render(self, intent: str, args: dict, objs: list, note: str | None = None) -> str:
        if intent.startswith("list_"):
            return self._list_preview(intent, args, note)
        if intent == "task_write":
            return self._task_write_preview(args)
        if intent == "add_event":
            drafts = [dict_to_draft(x) for x in args["drafts"]]
            if len(drafts) == 1:
                body = [i18n.t("preview_add")] + self._draft_lines(drafts[0])
            else:
                body = [i18n.t("preview_add_many", n=len(drafts))] + [f"- {d.title}, {self._when(d.start, d.end, d.all_day)}" for d in drafts[:30]]
            return "\n".join(body + ([note] if note else []) + [i18n.t("confirm_yes_no")])
        if intent == "edit_events":
            lines = [i18n.t("preview_edit_many", n=len(args["moves"]))]
            lines += [f"- {m['title']}: {m['before']} -> {_dt(m['start']).astimezone(self.tz):%H:%M}-{_dt(m['end']).astimezone(self.tz):%H:%M}"
                      for m in args["moves"]]
            return "\n".join(lines + [i18n.t("confirm_yes_no")])
        if intent == "edit_event":
            ch = args["changes"]
            after = [ch.get("title", args["title"])]
            if "start" in ch:
                after.append(self._when(_dt(ch["start"]), _dt(ch["end"]), ch.get("all_day", False)))
            for k, key in (("location", "p_location"), ("notes", "p_notes")):
                if k in ch:
                    after.append(i18n.t(key, x=ch[k]))
            if "reminder_minutes" in ch:
                after.append(i18n.t("p_reminder", x=i18n.fmt_left(ch["reminder_minutes"])))
            lines = [i18n.t("preview_edit"), i18n.t("before", x=f"{args['title']}, {args['before']}"),
                     i18n.t("after", x=", ".join(after)), i18n.t("p_all") if args["scope"] == "all" else ""]
            if "start" in ch:
                d = EventDraft(args["title"], _dt(ch["start"]), _dt(ch["end"]), ch.get("all_day", False))
                lines += [i18n.t("p_conflict", title=c.title, when=f"{c.start.astimezone(self.tz):%H:%M}-{c.end.astimezone(self.tz):%H:%M}")
                          for c in self._conflicts(d) if c.event_id != args["event_id"]][:2]
            return "\n".join([x for x in lines if x] + [i18n.t("confirm_yes_no")])
        lines = [i18n.t("preview_delete"), f"{args['title']}, {args['when']}",
                 i18n.t("p_all") if args["scope"] == "all" else (i18n.t("p_this_only") if (objs and objs[0].series_id) or args.get("recurring") else ""),
                 i18n.t("confirm_delete")]
        return "\n".join(x for x in lines if x)

    def _confirm(self, row, low: str, now: datetime) -> list[str]:
        if row is None:
            return [i18n.t("nothing_pending")]
        state = Store.pending_state(row, now)
        if state != "ok":
            self.store.close_pending(row["id"], state)
            self.store.audit("pending_" + state, {"id": row["id"]}, now, self.owner)
            return [i18n.t("pending_expired" if state == "expired" else "pending_tampered")]
        if (off := self.plugins.blocked("intents", row["intent"])):
            self.store.close_pending(row["id"], "blocked")          # the plugin was switched off after the preview was shown
            self.store.audit("write_blocked", {"id": row["id"], "intent": row["intent"], "plugin": off}, now, self.owner)
            return self._off(off)
        if row["intent"] == "delete_event" and low != "evet sil":
            return [i18n.t("need_evet_sil")]
        args = json.loads(row["args_json"])               # the STORED parameters, never re-derived from the chat
        if not self.store.claim_pending(row["id"]):        # atomic: a double tap or a tap racing 'evet' runs once
            return [i18n.t("nothing_pending")]
        self._noop = False
        try:
            reply = self._execute(row["intent"], args)
        except (WriteDisabled, WriteAccessDenied):
            self.store.close_pending(row["id"], "blocked")
            self.store.audit("write_blocked", {"id": row["id"], "intent": row["intent"]}, now, self.owner)
            return [i18n.t("write_disabled")]
        except Exception as exc:  # noqa: BLE001
            self.store.close_pending(row["id"], "failed")
            self.store.audit("write_failed", {"id": row["id"], "intent": row["intent"], "type": type(exc).__name__}, now, self.owner)
            return [i18n.t("write_failed")]
        self.store.close_pending(row["id"], "done")
        self._acted = True
        self.store.audit("write_noop" if self._noop else "write", {"id": row["id"], "intent": row["intent"]}, now, self.owner)
        return [reply]

    def _execute(self, intent: str, args: dict) -> str:
        if intent == "add_event":
            done, bad = [], 0
            for x in args["drafts"]:
                try:
                    done.append(self.hub.create_event(dict_to_draft(x)))
                except (WriteDisabled, WriteAccessDenied):
                    if not done:
                        raise
                    bad += 1
                except Exception:  # noqa: BLE001
                    if not done and len(args["drafts"]) == 1:
                        raise
                    bad += 1
            if not done:
                raise RuntimeError("nothing created")
            if bad:
                return i18n.t("done_added_partial", ok=len(done), bad=bad)
            closed = []
            for e in done:                         # a calendar event replaces the same open to-do: no duplicate in the list
                if args.get("keep_task"):
                    break                          # ...except a reminder ABOUT a task: the task stays open
                if (row := self._find_task(e.title)) is not None and taskmod.words_match(re.findall(r"\w+", fold(e.title)), row["title"]):
                    self.store.close_task(row["id"], "done", self.clock())
                    closed.append(row["title"])
            extra = ("\n" + i18n.t("task_closed_by_event", x=", ".join(closed))) if closed else ""
            if len(done) == 1:
                e = done[0]
                return i18n.t("done_added", x=f"{e.title}, {self._when(e.start, e.end, e.all_day)}") + extra
            return i18n.t("done_added_many", n=len(done)) + extra
        if intent == "edit_event":
            ch = dict(args["changes"])
            if "start" in ch:
                ch["start"], ch["end"] = _dt(ch["start"]), _dt(ch["end"])
            cur = self._event_by_id(args["event_id"])
            if cur is not None and self._same_state(cur, ch):      # no-op guard (ADR 0015): never write what is already there
                self._noop = True
                return i18n.t("done_noop", x=args["title"])
            self.hub.update_event(args["event_id"], ch)
            return i18n.t("done_edited", x=args["title"])
        if intent == "edit_events":
            ok = 0
            for m in args["moves"]:
                try:
                    self.hub.update_event(m["event_id"], {"start": _dt(m["start"]), "end": _dt(m["end"])})
                    ok += 1
                except (WriteDisabled, WriteAccessDenied):
                    if not ok:
                        raise
                except Exception:  # noqa: BLE001
                    if not ok and len(args["moves"]) == 1:
                        raise
            if not ok:
                raise RuntimeError("nothing moved")
            return i18n.t("done_edited_many", n=ok)
        if intent == "delete_event":
            self.hub.delete_event(args["event_id"])
            return i18n.t("done_deleted", x=f"{args['title']}, {args['when']}")
        if intent.startswith("list_"):
            return self._list_execute(intent, args)
        if intent == "task_write":
            return self._task_write_execute(args)
        raise RuntimeError("unknown intent")

    # ------------------------------------------------------------------ reads (computed in code)
    def _day_lines(self, evs: list[Event], day: datetime) -> list[str]:
        s = day.replace(hour=0, minute=0, second=0, microsecond=0)
        e = s + timedelta(days=1)
        out = []
        finished = self.store.done_event_ids(self.owner)
        for x in merge(evs):
            if not (x.start < e and x.end > s):
                continue
            if birthdays.is_birthday(x):
                out.append("- " + i18n.t("birthday_item", name=birthdays.person(x)))
                continue
            when = i18n.t("all_day") if x.all_day else f"{x.start.astimezone(self.tz):%H:%M}-{x.end.astimezone(self.tz):%H:%M}"
            flag = f" [{', '.join(i18n.FLAGS.get(f, f) for f in x.flags)}]" if x.flags else ""
            loc = f" @ {x.location}" if x.location else ""
            out.append(f"- {when} {'✓ ' if x.event_id in finished else ''}{x.title}{loc}{flag}")
        return out

    def _list(self, intent: dict, now: datetime) -> list[str]:
        self._read = True
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        r = intent.get("range", "today")
        if r == "date":
            d0 = datetime.combine(date.fromisoformat(intent["date"]), time(0, 0), self.tz)
            days = [d0]
        elif r == "tomorrow":
            days = [today + timedelta(days=1)]
        elif r in ("week", "upcoming"):
            days = [today + timedelta(days=i) for i in range(7 if r == "week" else 14)]
        else:
            days = [today]
        try:
            evs = self.hub.events(days[0], days[-1] + timedelta(days=1))
        except Exception:  # noqa: BLE001
            return [i18n.t("calendar_down")]
        out = []
        for d in days:
            lines = self._day_lines(evs, d)
            if lines:
                out += [i18n.t("list_head_day", date=i18n.fmt_date(d))] + lines
        tasks = self._plan_tasks(today) if r == "today" else []
        if not out:
            key = {"today": "list_empty_today", "tomorrow": "list_empty_tomorrow", "date": "list_empty_date"}.get(r, "list_empty_week")
            return ["\n".join([i18n.t(key, date=i18n.fmt_date(days[0]))] + tasks)]
        out += tasks
        errs = getattr(self.hub, "errors", [])
        if errs:
            out.append(i18n.t("unavailable", names=", ".join(errs)))
        return ["\n".join(out)]

    def _plan_tasks(self, today: datetime) -> list[str]:
        """The tasks section of a plan read, as in the morning digest: open tasks due today, overdue, or without a day."""
        iso = today.date().isoformat()
        rows = [r for r in self.store.open_tasks(self.owner) if not r["due"] or r["due"] <= iso]
        return [i18n.t("tasks_head")] + [i18n.t("task_line", x=r["title"]) for r in rows[:15]] if rows else []

    def _birthdays(self, now: datetime) -> list[str]:
        self._read = True
        try:
            evs = self.hub.events(now.replace(hour=0, minute=0, second=0, microsecond=0), now + timedelta(days=62))
        except Exception:  # noqa: BLE001
            return [i18n.t("calendar_down")]
        items = birthdays.upcoming(evs, now.date(), tuple(range(0, 61)))
        if not items:
            return [i18n.t("birthdays_none")]
        return ["\n".join([i18n.t("birthdays_list")] + [i18n.t("birthday_row", name=birthdays.person(e), date=i18n.fmt_date(e.start), when=birthdays.when_text(n)) for e, n in items])]

    # ------------------------------------------------------------------ bulk import (CLI)
    def propose_import(self, drafts: list[EventDraft], now: datetime, ttl: timedelta | None = None) -> list[str]:
        if not self.plugins.enabled("calendar"):
            return self._off("calendar")
        """One pending bulk add (same preview + 'evet' gate). `ttl` lets the owner answer later than 10 minutes."""
        return self._propose("add_event", {"drafts": [draft_to_dict(d) for d in drafts]}, drafts, now, ttl)

    def propose_list_add(self, key: str, items: list[str], now: datetime, ttl: timedelta | None = None) -> list[str]:
        if not self.plugins.enabled("lists"):
            return self._off("lists")
        """One pending add of items to a named list (operator seeding, `sebastian list-add`): same preview + confirm gate."""
        return self._propose("list_add", {"list": key, "items": items}, [], now, ttl)

    # ------------------------------------------------------------------ named lists (ADR 0016)
    def _list_title(self, key: str) -> str:
        return i18n.list_title(key)

    def _done_today(self, key: str, now: datetime) -> list:
        return [r for r in self.store.list_items(self.owner, key, "done")
                if r["closed_at"] and _dt(r["closed_at"]).astimezone(self.tz).date() == now.date()]

    def _list_view(self, key: str, now: datetime) -> str:
        """Numbered view: open items with an empty box, then today's checked items in a done section; yesterday's trip is gone."""
        open_, done = self.store.list_items(self.owner, key), self._done_today(key, now)
        title = self._list_title(key)
        self.shown_list = key
        if not open_ and not done:
            return i18n.t("lst_empty", list=title)
        lines, n = [i18n.t("lst_head", list=title)], 0
        for r in open_:
            n += 1
            lines.append(i18n.t("lst_row_open", n=n, x=r["text"]))
        if done:
            lines.append(i18n.t("lst_done_head"))
            for r in done:
                n += 1
                lines.append(i18n.t("lst_row_done", n=n, x=r["text"]))
        return "\n".join(lines)

    def _list_show(self, key: str, now: datetime) -> list[str]:
        return [self._list_view(key, now)]

    def _list_state(self, key: str) -> str:
        rows = [[r["id"], r["text"]] for r in self.store.list_items(self.owner, key)]
        return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()[:8]

    def _list_keyboard(self) -> dict | None:
        key = self.shown_list
        if not key or self.store.open_pending(self.owner):
            return None
        open_, done = self.store.list_items(self.owner, key), self._done_today(key, self.clock())
        if not open_:
            return None
        n, h = list(i18n.LISTS).index(key) + 1, self._list_state(key)
        rows = [[self._item_btn("i", "btn_item_open", r["id"], r["text"])] for r in open_]
        rows += [[self._item_btn("i", "btn_item_done", r["id"], r["text"])] for r in done]
        rows = rows[:ITEM_BUTTONS_MAX]
        rows.append([{"text": i18n.t("btn_all_bought"), "callback_data": f"a:{n}:{h}"},
                     {"text": i18n.t("btn_clear"), "callback_data": f"c:{n}:{h}"}])
        return {"inline_keyboard": rows}

    def _list_callback(self, action: str, n: int, h8: str, now: datetime) -> tuple[str | None, list[str]]:
        """The all-bought and clear buttons under a list view: verified against the CURRENT open items (a changed list is stale).
        'all bought' is the tap itself; 'clear' only opens the usual confirm preview."""
        keys = list(i18n.LISTS)
        if not 1 <= n <= len(keys) or h8 != self._list_state(keys[n - 1]) or self.store.open_pending(self.owner):
            return i18n.t("btn_stale"), []
        key = keys[n - 1]
        if action == "a":
            self.store.close_list_items([r["id"] for r in self.store.list_items(self.owner, key)], "done", now)
            self.store.audit("list_all_done", {"list": key}, now, self.owner)
            return None, [i18n.t("lst_all_checked") + "\n" + self._list_view(key, now)]
        return None, self._list_clear_preview(key, now)

    def _pick_items(self, key: str, match: str) -> list:
        """Open items a spoken reference means: numbers from the view, else an exact name, else the fuzzy matches."""
        open_ = self.store.list_items(self.owner, key)
        if (nums := listmod.numbers(match)) is not None:
            return [open_[i - 1] for i in nums if 1 <= i <= len(open_)]
        exact = [r for r in open_ if listmod.item_key(r["text"]) == fold(match).strip()]
        return exact or [r for r in open_ if title_match(match, r["text"])]

    def _list_command(self, ls: dict, now: datetime) -> list[str] | None:
        kind = ls["kind"]
        if kind == "show":
            return self._list_show(ls["list"], now)
        if kind == "open":
            self.store.set_conversation(self.owner, {"awaiting": "list", "list": ls["list"], "turns": 1, "at": now.isoformat(),
                                                     "question": i18n.t("lst_open_q", list=self._list_title(ls["list"]))})
            return [self._list_view(ls["list"], now) + "\n\n" + i18n.t("lst_open_q", list=self._list_title(ls["list"]))]
        if kind == "add":
            added, dups = self._list_add(ls["list"], ls["items"], now)
            head = [i18n.t("lst_added", list=self._list_title(ls["list"]), items=", ".join(added))] if added else []
            if dups:
                head.append(i18n.t("lst_dup", items=", ".join(dups)))
            return ["\n".join(head) + "\n\n" + self._list_view(ls["list"], now)]
        key = ls.get("list", i18n.DEFAULT_LIST)
        if kind == "check_all":
            open_ = self.store.list_items(self.owner, key)
            if not open_:
                return [i18n.t("lst_nothing_open")]
            self.store.close_list_items([r["id"] for r in open_], "done", now)
            return [i18n.t("lst_all_checked") + "\n" + self._list_view(key, now)]
        if kind == "check":
            hit, seen = [], set()
            for part in listmod.split_items(ls["match"]):       # 'kahve ekmek ve domates alindi': every item (clause rule)
                for r in (self._pick_items(key, part) or [x for w in part.split() for x in self._pick_items(key, w)]):
                    if r["id"] not in seen:
                        seen.add(r["id"])
                        hit.append(r)
            if not hit:
                return None                  # not a list item: the normal flow decides ("ekmek aldim" is no list command)
            self.store.close_list_items([r["id"] for r in hit], "done", now)
            return [i18n.t("lst_checked", items=", ".join(r["text"] for r in hit)) + "\n\n" + self._list_view(key, now)]
        if self.store.open_pending(self.owner):
            return None                      # a removal needs its own preview: not while another one is open
        if kind == "remove":
            hit = self._pick_items(key, ls["match"])
            if not hit:
                return [i18n.t("lst_not_found", x=ls["match"])]
            return self._propose("list_remove", {"list": key, "ids": [r["id"] for r in hit], "items": [r["text"] for r in hit]}, [], now)
        return self._list_clear_preview(key, now)

    def _list_clear_preview(self, key: str, now: datetime) -> list[str]:
        shown = self.store.list_items(self.owner, key) + self._done_today(key, now)
        if not shown:
            return [i18n.t("lst_clear_empty", list=self._list_title(key))]
        return self._propose("list_clear", {"list": key, "ids": [r["id"] for r in shown], "items": [r["text"] for r in shown]}, [], now)

    def _list_add(self, key: str, items: list[str], now: datetime) -> tuple[list[str], list[str]]:
        """Adds what is new; an item that is already open (same name, any quantity) is merged away and reported."""
        have = {listmod.item_key(r["text"]) for r in self.store.list_items(self.owner, key)}
        added, dups = [], []
        for it in items:
            k = listmod.item_key(it)
            if k in have:
                dups.append(it)
                continue
            have.add(k)
            self.store.add_list_item(self.owner, key, it, now)
            added.append(it)
        return added, dups

    def _list_preview(self, intent: str, args: dict, note: str | None) -> str:
        title = self._list_title(args["list"])
        rows = [i18n.t("lst_preview_row", x=x) for x in args["items"]]
        if intent == "list_add":
            head = i18n.t("lst_preview_add", list=title)
        elif intent == "list_remove":
            head = i18n.t("lst_preview_remove", list=title)
        else:
            head = i18n.t("lst_preview_clear", list=title, n=len(args["items"]))
        return "\n".join([head] + rows + ([note] if note else []) + [i18n.t("confirm_yes_no")])

    def _list_execute(self, intent: str, args: dict) -> str:
        key, now = args["list"], self.clock()
        self.shown_list = key
        if intent == "list_add":
            added, dups = self._list_add(key, args["items"], now)
            head = [i18n.t("lst_added", list=self._list_title(key), items=", ".join(added))] if added else []
            if dups:
                head.append(i18n.t("lst_dup", items=", ".join(dups)))
            return "\n".join(head) + "\n\n" + self._list_view(key, now)
        if intent == "list_remove":
            self.store.close_list_items(args["ids"], "removed", now)
            return i18n.t("lst_removed", items=", ".join(args["items"])) + "\n\n" + self._list_view(key, now)
        self.store.clear_list(self.owner, key, now)
        return i18n.t("lst_cleared", list=self._list_title(key))
