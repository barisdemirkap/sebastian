"""Statements about existing items (training loop). A mixin of `Assistant`: reads a message clause by clause (`clauses.py`),
resolves every subject against the open tasks and the calendar, and applies it.

Principles: (1) a reference to an existing event or task is resolved, never created again; (2) a completion closes the item and
never proposes deleting it; (3) every clause is processed (previews that cannot be open together are queued and announced);
(4) 'bu yarina' / 'yarina kalsin' / 'X'e tasi' reschedule, never close; (5) no stray 'type the number' questions."""
from __future__ import annotations
import json
import re
from datetime import date, datetime, timedelta

from . import assist as assistmod, birthdays, clauses as clausemod, i18n, lists as listmod, tasks as taskmod
from . import rules
from .merge import merge
from .models import Event
from .textutil import fold

WINDOW_DAYS = 8
PLAN_WORDS = {"toplanacak", "yapilacak", "duzenlenecek", "yapacagim"}
MODEL_FORCES_PREVIEW = True      # a task write made from a model reading always waits for "evet" (the spike script turns this off to score labels)
STAY_VERB = re.compile(r"\b(?:kalsin|kaldi|kalacak|birak\w*)\b")


class StatementsMixin:
    # ------------------------------------------------------------------ resolution
    def _events_window(self, now: datetime) -> list[Event]:
        d0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            evs = merge(self.hub.events(d0, d0 + timedelta(days=WINDOW_DAYS)))
        except Exception:  # noqa: BLE001
            return []
        return [e for e in evs if e.event_id and e.calendar != "birthdays" and (not birthdays.is_birthday(e) or re.search(r"parti|kutlama|davet|yemegi", fold(e.title)))]       # a birthday PARTY is an event like any other

    @staticmethod
    def _find(name: str, tasks: list, events: list[Event], ratio: float) -> dict:
        """Tasks and events the name points at: only the closest names (a name that is merely a shared stem, 'toplanti' vs 'topla', loses)."""
        ts = [(clausemod.score(name, r["title"], ratio), r) for r in tasks]
        es = [(clausemod.score(name, e.title, ratio), e) for e in events]
        top = max([sc for sc, _ in ts + es], default=0)
        return {"name": name, "tasks": [r for sc, r in ts if sc and sc >= top - .15], "events": [e for sc, e in es if sc and sc >= top - .15]}

    def _groups(self, subject: str, tasks: list, events: list[Event], ratio: float = .6, loose: bool = True) -> list[dict]:
        """The subject as one name, else (when that matches nothing) each 'A ve B' part as its own name."""
        if not subject:
            return []
        whole = self._find(subject, tasks, events, ratio)
        parts = [self._find(p.strip(), tasks, events, ratio) for p in re.split(r"\s+ve\s+", subject) if p.strip()]
        if len(parts) > 1 and all(g["tasks"] or g["events"] for g in parts):
            seen: set = set()                                  # 'Alexa ve Hue kurulumlari' may match one item through both parts
            out = []
            for g in parts:
                g["tasks"] = [r for r in g["tasks"] if ("t", r["id"]) not in seen and not seen.add(("t", r["id"]))]
                g["events"] = [e for e in g["events"] if ("e", e.event_id) not in seen and not seen.add(("e", e.event_id))]
                if g["tasks"] or g["events"]:
                    out.append(g)
            return out
        if whole["tasks"] or whole["events"]:
            return [whole]
        if len(parts) > 1 and any(g["tasks"] or g["events"] for g in parts):
            return parts
        if loose and ratio >= .6 and len(subject.split()) >= 2:                    # an unknown word in front ('vallahi cicekleri'): one clear best item is still the one meant
            loose = self._find(subject, tasks, events, .3)
            if len(loose["tasks"]) + len(loose["events"]) == 1:
                return [loose]
        return [whole]

    # ------------------------------------------------------------------ entry
    def _statements(self, text: str, now: datetime) -> list[str] | None:
        """Handle a message made of statements about existing items; None when the older flows should read it."""
        if self._in_clauses:
            return None
        today = now.date()
        cls = clausemod.interpret(text, today)
        if not cls:
            alt = self._assist(text, [], [], False, now)
            return self._apply_model(alt, text, now) if alt is not None else None
        cls = self._follow_up(cls, now)
        if len(cls) == 1 and cls[0]["kind"] in ("info", "other") and cls[0]["time"] and re.search(r"\bdedim\b", fold(cls[0]["raw"])) and (title := self._last_item(now)):
            h, m = cls[0]["time"]                           # 'sabah 7.30 dedim ya': repeats the time of the item just talked about
            for e in self._events_window(now):
                if clausemod.match(title, e.title) and (e.start.astimezone(self.tz).hour, e.start.astimezone(self.tz).minute) == (h, m):
                    self._acted = True
                    return [self._already_text(e)]
        tasks, events = list(self.store.open_tasks(self.owner)), self._events_window(now)
        plan, prev = [], ""
        for c in cls:
            subject = c["subject"] or (prev if c["kind"] == "remind" else "")
            if not subject and c["kind"] in ("remind", "defer", "done", "plan") and re.search(r"\b(?:bunu|onu|sunu)\b", fold(c["raw"])):
                subject = self._last_item(now)                 # 'bunu bu aksam 10 gibi hatirlat': the item talked about last
            prev = subject or prev
            plan.append((c, subject, self._groups(subject, tasks, events, .4 if c["kind"] == "forgot" else .6, c["kind"] != "add")))
        if len(plan) == 1 and plan[0][0]["kind"] == "other" and (plan[0][0]["day"] or plan[0][0]["time"]) and "?" not in plan[0][0]["raw"]:
            c0, subj0, gs0 = plan[0]
            if (subj0 := clausemod._subject(c0["raw"].split())) and len(subj0.split()) <= 4 and not re.search(r"\bsonra\b", fold(c0["raw"])) and (gs0 := self._groups(subj0, tasks, events)) and any(g["tasks"] for g in gs0) \
                    and not any(g["events"] for g in gs0):
                plan = [(dict(c0, kind="plan" if c0["time"] else "defer", subject=subj0), subj0, gs0)]       # 'balkon temizligi cuma' with an open task of that name: it moves to that day
        claims = self._claims(plan)
        if (alt := self._assist(text, cls, plan, claims, now)) is not None and (out := self._apply_model(alt, text, now)) is not None:
            return out                                           # a model answer that cannot be applied leaves the rules' reading standing
        if not claims:
            return None
        no_hit = not any(g["tasks"] or g["events"] for _, _, gs in plan for g in gs)
        if no_hit and (self.store.open_pending(self.owner) or (any(c["scope"] for c, _, _ in plan) and not tasks)):
            return None                       # 'hepsi yarin olsun' while a preview is open edits that preview; scope words need open tasks
        before = self.store.get_conversation(self.owner)
        self.store.clear_conversation(self.owner)
        out = self._apply(plan, text, now)
        if out is None and before:
            self.store.set_conversation(self.owner, before)
        elif out is not None:
            if touched_title := next((r["title"] for _, _, gs in plan for g in gs for r in g["tasks"]), None):
                self.store.set_meta("last_item", json.dumps({"title": touched_title, "at": now.isoformat()}))
            acts = [c for c, _, _ in plan if c["kind"] in ("done", "defer")]
            if acts:
                self.store.set_meta("last_stmt", json.dumps({"kind": acts[-1]["kind"], "day": acts[-1]["day"].isoformat() if acts[-1]["day"] else None,
                                                             "at": now.isoformat()}))
        return out

    # ------------------------------------------------------------------ model assist (ADR 0023): rules first, shadow by default
    def _assist_low(self, text: str, cls: list[dict], plan: list, claims: bool) -> bool:
        """The rules are not confident: nothing to act on, an unreadable clause, or an acting clause whose reference resolved to nothing.
        Trivial messages, questions, commands, explicit older wordings and anything while a preview or question is open never qualify."""
        if len(text.split()) < 3 or text.lstrip().startswith("/") or "?" in text or self.store.open_pending(self.owner) or self.store.get_conversation(self.owner):
            return False
        if taskmod.parse(text) or listmod.parse(text):
            return False
        p = rules.parse(text, self.clock().date())
        if p["intent"] in ("list_events", "birthdays", "help", "chitchat") or (p["intent"] == "add_event" and p.get("title") and p.get("date")):
            return False
        if not claims:
            return True
        hit = lambda gs: any(g["tasks"] or g["events"] for g in gs)                    # noqa: E731
        for c, _, gs in plan:
            if c["kind"] == "other" and not (c["day"] or c["time"]):
                return True
            if c["kind"] in ("done", "defer", "delete", "forgot", "keep") and not c["scope"] and not hit(gs):
                return True
        return False

    def _assist(self, text: str, cls: list[dict], plan: list, claims: bool, now: datetime) -> list[dict] | None:
        """Consults the model for a low-confidence message. Shadow mode (default): logs the answer and its comparison with the rules and
        returns None. Promoted mode: returns the validated model clauses. Any failure (timeout, bad id, schema) falls back to the rules."""
        svc = getattr(self, "assist", None)
        if svc is None or not svc.active or self._in_clauses or self.readonly or not self._assist_low(text, cls, plan, claims):
            return None
        if self.store.nl_calls(now.date().isoformat(), self.owner) >= self.nl.daily_cap:
            return None
        tasks, events = list(self.store.open_tasks(self.owner)), self._events_window(now)
        cands = assistmod.candidates(tasks, events)
        rules_said = "; ".join(f"{c['kind']}:{c['subject']}" for c in cls) or "nothing"
        self.store.nl_bump(now.date().isoformat(), self.owner)
        detail = {"mode": svc.mode, "model": svc.provider.model, "temperature": svc.provider.temperature_note, "rules_claimed": claims,
                  "rules": [(c["kind"], c["subject"]) for c in cls]}
        try:
            raw, meta = svc.provider.call(assistmod.build_prompt(text, cands, self.tz, rules_said))
            detail.update(ms=meta["ms"], cost_usd=meta["cost_usd"], model=meta["model"], raw=meta["raw"])
            out = assistmod.validate(raw, text, cands, now.date())
        except assistmod.AssistError as exc:
            detail["rejected"] = exc.reason
            self.store.audit("nl_assist", detail, now, self.owner)
            return None
        rule_ids = sorted({f"T{r['id']}" for _, _, gs in plan for g in gs for r in g["tasks"]})
        detail.update(model_clauses=assistmod.summarize(out), rules_task_ids=rule_ids,
                      agree_kinds=[c["kind"] for c in cls] == [c["kind"] for c in out],
                      agree_ids=rule_ids == sorted({i for c in out for i in c["ids"] if i.startswith("T")}))
        self.store.audit("nl_assist", detail, now, self.owner)
        return out if svc.mode == "on" else None

    def _apply_model(self, model_cls: list[dict], text: str, now: datetime) -> list[str] | None:
        """A promoted model answer: the clauses carry resolved candidate ids, so no fuzzy name matching happens here. Task writes made from
        a model reading always show a preview and wait for 'evet' (`force`)."""
        tasks = {f"T{r['id']}": r for r in self.store.open_tasks(self.owner)}
        events = {f"E{i}": e for i, e in enumerate(self._events_window(now)[:30], 1)}
        plan = []
        for c in model_cls:
            gs = [{"name": c["subject"], "tasks": [tasks[i] for i in c["ids"] if i in tasks], "events": [events[i] for i in c["ids"] if i in events]}] if c["ids"] else []
            if c["kind"] in ("read", "other", "add"):
                return None                                  # not an action the model may take (or it is unsure): the rules' answer stands
            if c["kind"] != "info":                          # a remark needs no action ('gec oldu'); the other clauses still apply
                plan.append((c, c["subject"], gs))
        if not plan:
            return None
        before = self.store.get_conversation(self.owner)
        self.store.clear_conversation(self.owner)
        out = self._apply(plan, text, now)
        if out is None and before:
            self.store.set_conversation(self.owner, before)
        return out

    def _last_item(self, now: datetime) -> str:
        try:
            last = json.loads(self.store.get_meta("last_item", "") or "null")
            if last and now - datetime.fromisoformat(last["at"]) <= timedelta(minutes=30):
                return last["title"]
        except (ValueError, TypeError, KeyError):
            pass
        return ""

    def _follow_up(self, cls: list[dict], now: datetime) -> list[dict]:
        """'pencereleri de' right after 'dolabi yarina birak': the same statement for one more item."""
        if len(cls) != 1 or cls[0]["kind"] != "other" or cls[0]["day"] or cls[0]["time"]:
            return cls
        c = cls[0]
        ws = c["raw"].split()
        if len(ws) < 2 or fold(ws[-1]) not in ("de", "da") or "?" in c["raw"]:
            return cls
        try:
            last = json.loads(self.store.get_meta("last_stmt", "") or "null")
            if not last or now - datetime.fromisoformat(last["at"]) > timedelta(minutes=15):
                return cls
        except (ValueError, TypeError):
            return cls
        subject = clausemod._subject(ws)
        if not subject:
            return cls
        day = date.fromisoformat(last["day"]) if last.get("day") else None
        return [dict(c, kind=last["kind"], subject=subject, day=day)]

    @staticmethod
    def _legacy_reads(raw: str) -> bool:
        """The older flows own this wording (a task 'bitti', a list 'aldim')."""
        from . import lists as listmod
        tk = taskmod.parse(raw)
        return bool(tk and tk["kind"] in ("done", "add")) or listmod.parse(raw) is not None

    def _claims(self, plan: list) -> bool:
        hit = lambda gs: any(g["tasks"] or g["events"] for g in gs)                    # noqa: E731
        if len(plan) == 1:
            c, _, gs = plan[0]
            k = c["kind"]
            if k == "read_remaining" and re.search(r"yapilacak|yapilmasi gereken", fold(c["raw"])):
                return True                                    # 'bugun yapilacak islerim neler': events AND to-dos
            if k in ("read_remaining", "read_day", "info", "keep"):
                return False
            if k == "remind":
                return any(g["tasks"] for g in gs) and bool(c["time"])
            if k == "defer":
                return hit(gs) or bool(c["scope"]) or bool(STAY_VERB.search(fold(c["raw"])) and c["subject"])
            if k == "delete":
                return any(g["events"] for g in gs) or (hit(gs) and bool(re.search(r"iptal|\bgerek\b", fold(c["raw"]))))      # plain 'sil/kaldir' of a task is the older flow's (own wording)
            if k == "plan":
                if c["time"] and not c.get("remind"):
                    return any(g["tasks"] for g in gs) and not any(g["events"] for g in gs)      # 'cop aksam cikacak 18.30da': an open task gets its time
                return bool(c["day"]) and not c.get("remind") and hit(gs)            # a plan without a day is the older flows' business
            if k == "done" and c["scope"]:
                return True
            if k == "done" and not hit(gs):
                return not self._legacy_reads(c["raw"])        # a done word the older flows do not know: say it found nothing, never a new task
            return hit(gs)
        acting = [(c, gs) for c, _, gs in plan if c["kind"] in ("done", "defer", "partial", "delete", "forgot", "keep") or c["scope"]]
        reads = any(c["kind"] in ("read_remaining", "read_day") for c, _, _ in plan)
        if any(c["kind"] == "other" and not (c["day"] or c["time"] or "?" in c["raw"] or listmod.parse(c["raw"]) or taskmod.parse(c["raw"])) for c, _, _ in plan) \
                and not (len(plan) <= 3 and any(c["kind"] in ("done", "defer", "forgot") and hit(gs) for c, _, gs in plan)):
            return False                       # a clause nobody here can read: the older flows (meta questions, ready-time edits) see the whole message
        if not acting and not reads and not self.nl.enabled:        # with a model on, it reads several new events; the rules do it only when there is none
            dated = [c for c, _, _ in plan if c["kind"] == "other" and (c["day"] or c["time"]) and fold(c["raw"]).endswith(" var")]
            return len(dated) >= 2 and all(c in dated or c["kind"] == "plan" for c, _, _ in plan)   # 'yarin 10da toplanti var cuma 15te dis hekimi var': one preview at a time
        if not acting and not reads:
            return False
        return reads or (bool(acting) and any(hit(gs) or c["scope"] for c, gs in acting))

    # ------------------------------------------------------------------ apply
    def _apply(self, plan: list, text: str, now: datetime) -> list[str] | None:
        today = now.date()
        touched: set[int] = set()
        lines: list[str] = []
        reads: list[str] = []
        actions: list[dict] = []
        forgot: dict | None = None
        multi = len(plan) > 1
        last_done = False
        day_text = lambda d: i18n.fmt_date_full(d)                                    # noqa: E731

        def due_for(rows, day: date, force: bool = False) -> None:
            if len(rows) > 1 or force:                                  # several tasks fit the name: preview, never a silent bulk move
                touched.update(r["id"] for r in rows)
                actions.append({"op": "task_write", "args": self._task_write_args("defer", rows, day), "say": ", ".join(r["title"] for r in rows)})
                return
            for r in rows:
                self.store.set_task_due(r["id"], day.isoformat())
                touched.add(r["id"])
                lines.append(i18n.t("st_deferred", x=r["title"], d=day_text(day)))
                self._acted = True

        for c, subject, gs in [p for p in plan if not p[0]["scope"]] + [p for p in plan if p[0]["scope"]]:
            k, day = c["kind"], c["day"]
            if k != "done":
                last_done = False
            if c["scope"]:
                rows = [r for r in self.store.open_tasks(self.owner) if r["id"] not in touched and (not r["due"] or r["due"] <= today.isoformat())]
                if k == "done" and not c.get("model") and re.search(r"\bgecmis\b|\bgecen\w*", fold(c["raw"])):
                    past = [e for e in self._events_window(now) if e.start.astimezone(self.tz).date() == today and e.end <= now]
                    if past:
                        actions.append({"op": "task_write", "args": self._task_write_args("done", [], None, past), "say": ", ".join(e.title for e in past)})
                    else:
                        lines.append(i18n.t("st_no_past"))
                elif k in ("done", "defer") and rows:
                    touched.update(r["id"] for r in rows)
                    actions.append({"op": "task_write", "args": self._task_write_args("done" if k == "done" else "defer", rows, day),
                                    "say": ", ".join(r["title"] for r in rows)})
                continue
            if k == "read_remaining":
                reads += self._remaining(now)
            elif k == "read_day":
                reads += self._list({"intent": "list_events", "range": "tomorrow" if day == today + timedelta(days=1) else "date",
                                     "date": day.isoformat(), "end_date": None}, now)
            elif k == "info":
                continue
            elif k == "other":
                if multi and not (c["day"] or c["time"] or "?" in c["raw"] or listmod.parse(c["raw"]) or taskmod.parse(c["raw"])):
                    continue                              # chatter next to resolved statements ('cok sukur', 'hava sogudu'): nothing to do
                actions.append({"op": "legacy", "text": c["raw"], "say": c["raw"]})
            elif k == "add":
                for g in gs or [{"name": subject, "tasks": [], "events": []}]:
                    if g["tasks"]:
                        lines.append(i18n.t("st_plan_known", x=g["tasks"][0]["title"]))
                    else:
                        title = taskmod._cap(g["name"])
                        touched.add(self.store.add_task(self.owner, title, now, None))
                        lines.append(i18n.t("task_added", x=title))
                        self._acted = True
            elif k == "keep":
                for g in gs:
                    touched.update(r["id"] for r in g["tasks"])
                    if g["tasks"]:
                        lines.append(i18n.t("st_kept", x=", ".join(r["title"] for r in g["tasks"])))
            elif k == "done":
                for g in gs:
                    evs = self._finished_events(g["events"], now)
                    if len(g["tasks"]) > 1 or (c.get("model") and MODEL_FORCES_PREVIEW and g["tasks"]):        # a name that fits several tasks is not 'clearly resolved': show them, ask
                        touched.update(r["id"] for r in g["tasks"])
                        actions.append({"op": "task_write", "args": self._task_write_args("done", g["tasks"]), "say": ", ".join(r["title"] for r in g["tasks"])})
                    for r in g["tasks"][:1] if len(g["tasks"]) == 1 and not (c.get("model") and MODEL_FORCES_PREVIEW) else []:
                        self.store.close_task(r["id"], "done", now)
                        touched.add(r["id"])
                        lines.append(i18n.t("st_done", x=r["title"]))
                        self._acted = True
                    for e in evs:
                        self.store.mark_event_done(self.owner, e.event_id, e.title, now)
                        lines.append(i18n.t("st_done_event", x=e.title))
                        self._acted = True
                    if not g["tasks"] and not evs and multi and listmod.parse(c["raw"]) is not None:
                        actions.append({"op": "legacy", "text": c["raw"], "say": c["raw"]})            # 'sut aldim': a list item, the list flow checks it off
                    elif not g["tasks"] and not evs and multi and not last_done:
                        lines.append(i18n.t("st_unknown", x=g["name"]))
                    last_done = bool(g["tasks"] or evs)
            elif k == "partial":
                base = ""
                for g in gs:
                    for r in g["tasks"]:
                        self.store.close_task(r["id"], "done", now)
                        touched.add(r["id"])
                        base = base or r["title"]
                    for e in self._finished_events(g["events"], now):
                        self.store.mark_event_done(self.owner, e.event_id, e.title, now)
                base = base or taskmod._cap(subject)
                stem = " ".join(w for w in base.split() if fold(w) not in PLAN_WORDS)
                title = taskmod._cap(i18n.t("st_partial_title", x=stem or base, y=c["rest"]))
                tid = self.store.add_task(self.owner, title, now, day.isoformat() if day else None)
                touched.add(tid)
                self._acted = True
                lines.append(i18n.t("st_partial", x=base, y=title))
            elif k == "defer":
                for g in gs:
                    if g["tasks"]:
                        due_for(g["tasks"], day, bool(c.get("model")) and MODEL_FORCES_PREVIEW)
                    cands = sorted((e for e in g["events"] if e.start.astimezone(self.tz).date() != day), key=lambda e: e.start)
                    if cands:
                        actions.append({"op": "move", "event_ids": [e.event_id for e in cands], "title": cands[0].title, "date": day.isoformat(),
                                        "say": i18n.t("q_move", x=cands[0].title, d=day_text(day))})
                    if g["events"] and not g["tasks"] and not cands:
                        lines.append(i18n.t("st_already", x=g["events"][0].title))
                    if not g["tasks"] and not g["events"]:
                        if STAY_VERB.search(fold(c["raw"])):
                            title = taskmod._cap(g["name"])
                            touched.add(self.store.add_task(self.owner, title, now, day.isoformat()))
                            lines.append(i18n.t("st_added_due", x=title, d=day_text(day)))
                            self._acted = True
                        else:
                            lines.append(i18n.t("st_unknown", x=g["name"]))
            elif k == "plan":
                for g in gs or [{"name": subject, "tasks": [], "events": []}]:
                    had_events = bool(g["events"]) or any(clausemod.match(subject, e.title) for e in self._events_window(now))
                    g = dict(g, events=[e for e in g["events"] if day and e.start.astimezone(self.tz).date() == day])     # only a calendar entry of THAT day is 'already there'
                    if c["time"] and g["tasks"] and not had_events:
                        r, (h, m) = g["tasks"][0], c["time"]
                        d = day or (today if (h, m) > (now.hour, now.minute) else today + timedelta(days=1))
                        actions.append({"op": "remind", "replace": True, "title": r["title"], "date": d.isoformat(), "time": f"{h:02d}:{m:02d}", "say": i18n.t("q_remind", x=r["title"], t=f"{h:02d}:{m:02d}")})
                    elif day and g["tasks"] and not c["time"]:
                        due_for(g["tasks"], day)
                    elif day == today and not g["events"] and not c["time"]:
                        title = taskmod._cap(g["name"])
                        touched.add(self.store.add_task(self.owner, title, now, day.isoformat()))
                        lines.append(i18n.t("st_added_due", x=title, d=day_text(day)))
                        self._acted = True
                    else:
                        actions.append({"op": "legacy", "text": c["raw"], "say": c["raw"]})      # no day: the older flows (time moves, dup check)
                        break
            elif k == "remind":
                g = gs[0] if gs else None
                title = (g["tasks"][0]["title"] if g and g["tasks"] else g["events"][0].title if g and g["events"] else taskmod._cap(subject))
                if not c["time"] or not title:
                    actions.append({"op": "legacy", "text": c["raw"], "say": c["raw"]})
                    continue
                h, m = c["time"]
                d = day or (today if (h, m) > (now.hour, now.minute) else today + timedelta(days=1))
                actions.append({"op": "remind", "title": title, "date": d.isoformat(), "time": f"{h:02d}:{m:02d}", "say": i18n.t("q_remind", x=title, t=f"{h:02d}:{m:02d}")})
            elif k == "delete":
                for g in gs:
                    days = {day, day + timedelta(days=-7)} if day and day == today + timedelta(days=7) else {day}
                    evs = sorted((e for e in g["events"] if not day or e.start.astimezone(self.tz).date() in days), key=lambda e: e.start) or g["events"]
                    if evs:
                        actions.append({"op": "delete", "event_ids": [e.event_id for e in evs], "title": evs[0].title, "say": i18n.t("q_delete", x=evs[0].title)})
                    if not evs and g["tasks"]:
                        touched.update(r["id"] for r in g["tasks"])
                        actions.append({"op": "task_write", "args": self._task_write_args("removed", g["tasks"]), "say": ", ".join(r["title"] for r in g["tasks"])})
                    elif not evs:
                        lines.append(i18n.t("st_unknown", x=g["name"]))
            elif k == "forgot":
                forgot = {"tasks": [r["id"] for g in gs for r in g["tasks"]], "events": [e.event_id for g in gs for e in g["events"][:1]],
                          "name": subject}
        lines = list(dict.fromkeys(lines))                         # two clauses may name the same item
        out = (["\n".join(lines)] if lines else []) + reads
        queued: list[dict] = []
        for a in actions:
            if queued or self.store.open_pending(self.owner) or self.store.get_conversation(self.owner):
                queued.append(a)
                continue
            out += self._run_action(a, now)
        if forgot and (forgot["tasks"] or forgot["events"]) and not queued and not self.store.open_pending(self.owner):
            q = i18n.t("st_forgot_ask", x=forgot["name"])
            self.store.set_conversation(self.owner, {"awaiting": "defer_when", "tasks": forgot["tasks"], "events": forgot["events"], "question": q,
                                                     "turns": 1, "at": now.isoformat()})
            out.append(q)
        if queued:
            self.store.set_meta("action_queue", json.dumps({"at": now.isoformat(), "items": queued}))
            out.append(i18n.t("clause_queued", x="; ".join(a["say"] for a in queued)))
        if not out and len(plan) == 1 and plan[0][0]["kind"] == "done":
            return [i18n.t("task_none")]
        return out or None

    # ------------------------------------------------------------------ task writes that need a preview ('evet')
    def _task_write_args(self, op: str, rows: list, day: date | None = None, events: list[Event] | None = None) -> dict:
        return {"op": op, "ids": [r["id"] for r in rows], "titles": [r["title"] for r in rows], "day": day.isoformat() if day else None,
                "events": [{"id": e.event_id, "title": e.title} for e in events or []]}

    def _task_write_preview(self, args: dict) -> str:
        key = {"removed": "pv_task_removed", "done": "pv_task_done", "defer": "pv_task_deferred"}[args["op"]]
        head = i18n.t(key, d=i18n.fmt_date_full(date.fromisoformat(args["day"]))) if args.get("day") else i18n.t(key)
        rows = [i18n.t("lst_preview_row", x=x) for x in args["titles"]] + [i18n.t("lst_preview_row", x=f"{e['title']} {i18n.t('pv_task_event')}") for e in args.get("events", [])]
        return "\n".join([head] + rows + [i18n.t("confirm_yes_no")])

    def _task_write_execute(self, args: dict) -> str:
        """Applies the STORED ids; a task that is no longer open is skipped and never re-resolved from the chat."""
        now = self.clock()
        open_ids = {r["id"]: r["title"] for r in self.store.open_tasks(self.owner)}
        hit = [(i, open_ids[i]) for i in args["ids"] if i in open_ids]
        evs = args.get("events", [])
        if not hit and not evs:
            self._noop = True
            return i18n.t("ex_task_gone")
        op = args["op"]
        for i, _ in hit:
            if op == "defer":
                self.store.set_task_due(i, args["day"])
            else:
                self.store.close_task(i, "removed" if op == "removed" else "done", now)
        for e in evs:
            self.store.mark_event_done(self.owner, e["id"], e["title"], now)
        names = ", ".join([t for _, t in hit] + [e["title"] for e in evs])
        if op == "defer":
            return i18n.t("ex_task_deferred", d=i18n.fmt_date_full(date.fromisoformat(args["day"])), items=names)
        return i18n.t("ex_task_removed" if op == "removed" else "ex_task_done", items=names)

    def _finished_events(self, events: list[Event], now: datetime) -> list[Event]:
        """The event a 'done' statement is about: the latest one of today that has started (else a single one of today)."""
        today = [e for e in events if e.start.astimezone(self.tz).date() == now.date()]
        started = sorted((e for e in today if e.start <= now), key=lambda e: e.start)
        return started[-1:] or (today if len(today) == 1 else [])

    # ------------------------------------------------------------------ actions that open a preview (one at a time)
    def _run_action(self, a: dict, now: datetime) -> list[str]:
        op = a["op"]
        if op == "legacy":
            if (ls := listmod.parse(a["text"])) and (out := self._list_command(ls, now)) is not None:
                return out
            self._in_clauses = True
            try:
                return self._new(a["text"], now) or []
            finally:
                self._in_clauses = False
        if op == "task_write":
            return self._propose("task_write", a["args"], [], now)
        if op == "remind":
            it = {"intent": "add_event", "title": a["title"], "date": a["date"], "time": a["time"], "end_time": None, "duration_min": 30, "all_day": False,
                  "reminder_min": 0, "location": None, "notes": None, "recurrence": "", "tag": "", "_orig": a["title"], "_keep_task": not a.get("replace")}
            return self._add(it, a["title"], now, 1)
        evs = [e for e in (self._event_by_id(i) for i in a["event_ids"]) if e is not None]
        if not evs:
            return []
        intent = ({"intent": "delete_event", "match": a["title"], "date": None, "scope": "this"} if op == "delete" else
                  {"intent": "edit_event", "match": a["title"], "date": None, "scope": "this", "changes": {k: a[k] for k in ("date", "time") if a.get(k)}})
        return self._offer_pick(intent, evs, now) if len(evs) > 1 else self._propose_target(intent, evs[0], now)

    def _drain_actions(self, now: datetime) -> list[str]:
        """The next queued action of a multi-clause message, once nothing is open any more (one preview at a time)."""
        raw = self.store.get_meta("action_queue", "")
        if not raw or self.store.open_pending(self.owner) or self.store.get_conversation(self.owner):
            return []
        q = json.loads(raw)
        items = q.get("items") or []
        if not items or now - datetime.fromisoformat(q["at"]) > timedelta(hours=2):
            self.store.set_meta("action_queue", "")
            return []
        self.store.set_meta("action_queue", json.dumps({"at": q["at"], "items": items[1:]}) if items[1:] else "")
        out = self._run_action(items[0], now)
        while not out and (more := self.store.get_meta("action_queue", "")):      # an action that shows nothing: go on with the next
            out = self._drain_actions(now)
            break
        return out

    # ------------------------------------------------------------------ the answer to 'moved to the evening or tomorrow?'
    def _defer_when(self, conv: dict, text: str, now: datetime) -> list[str]:
        f = fold(text)
        ws = [clausemod._w(t) for t in text.split()]
        today = now.date()
        tm = None
        if re.search(r"\baksam\w*", f):
            day, tm = today, "18:00"
        else:
            day = clausemod._day(ws, today)
        if day is None:
            conv["turns"] = conv.get("turns", 1) + 1
            self.store.set_conversation(self.owner, conv)
            return [conv.get("question") or i18n.t("pick_again")]
        self.store.clear_conversation(self.owner)
        rows = [r for r in self.store.open_tasks(self.owner) if r["id"] in conv.get("tasks", [])]
        lines = []
        for r in rows:
            self.store.set_task_due(r["id"], day.isoformat())
            lines.append(i18n.t("st_deferred", x=r["title"], d=i18n.fmt_date_full(day)))
            self._acted = True
        out = ["\n".join(lines)] if lines else []
        for eid in conv.get("events", []):
            if ev := self._event_by_id(eid):
                ch = {"date": day.isoformat()}
                if tm:
                    ch["time"] = tm
                out += self._propose_target({"intent": "edit_event", "match": ev.title, "date": None, "scope": "this", "changes": ch}, ev, now)
                break
        return out or [i18n.t("st_info", x=text[:60])]
