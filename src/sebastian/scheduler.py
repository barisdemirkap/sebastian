"""Tick-based scheduler: fires morning/evening digests once per day and sends reminders.

State (what already fired) is a JSON file so restarts never double-send."""
from __future__ import annotations
import json
import time as _time
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from pathlib import Path
from typing import Callable
from .plugins import Plugins
from .digest import morning_digest, evening_digest, plan_digest
from . import i18n
from .mail import MailMessage
from .models import Event
from .notify import Notifier
from .reminders import due_reminders, format_reminder, reminder_key

CATCH_UP = timedelta(hours=2)  # a digest missed by more than this is skipped, not sent late


class State:
    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict = {"digests": {}, "reminded": []}
        if path and path.exists():
            self.data.update(json.loads(path.read_text()))

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data))


@dataclass
class Scheduler:
    morning: time
    evening: time
    fetch: Callable[[datetime], list[Event]]   # now -> events for the relevant window
    notifier: Notifier
    state: State
    lead: timedelta = timedelta(minutes=30)
    mail_fetch: Callable[[datetime], list[MailMessage]] | None = None  # Phase 2, optional
    owners: tuple[str, ...] = ()
    plan: time | None = None            # 22:00 "tomorrow morning plan"; None disables it
    allday_at: time = time(9, 0)        # reminder time for all-day events
    tasks: Callable[[], list[str]] | None = None   # open to-do titles for the morning and evening digests (ADR 0013)
    lists: Callable[[], list[tuple[str, list[str]]]] | None = None   # (list title, open items) for shopping days (ADR 0016)
    nag: Callable[[datetime], tuple[time, list[str]] | None] | None = None   # (start, open task lines) while an hourly task nag is on
    plugins: Plugins = Plugins()        # ADR 0005: a switched-off plugin drops out of the digests, reminders and nag

    def _due_digests(self, now: datetime) -> list[str]:
        out = []
        slots = [("morning", self.morning), ("evening", self.evening)] + ([("plan", self.plan)] if self.plan else [])
        for kind, t in slots:
            at = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            key = f"{kind}:{now.date().isoformat()}"
            if at <= now < at + CATCH_UP and key not in self.state.data["digests"]:
                out.append(kind)
        return out

    def _mail(self, now: datetime) -> list[MailMessage] | None:
        try:
            return self.mail_fetch(now)
        except Exception:  # noqa: BLE001 - mail must never block the calendar digest
            return None

    def _tasks(self) -> list[str]:
        try:
            return self.tasks() if self.tasks and self.plugins.enabled("tasks") else []
        except Exception:  # noqa: BLE001 - the to-do list must never block a digest
            return []

    def _lists(self) -> list[tuple[str, list[str]]]:
        try:
            return self.lists() if self.lists and self.plugins.enabled("lists") else []
        except Exception:  # noqa: BLE001 - the lists must never block a digest
            return []

    def _deliver(self, text: str, sent: list[str]) -> bool:
        """Send one message; a failure leaves it unmarked so the next tick retries it (and only it)."""
        try:
            self.notifier.send(text)
        except Exception:  # noqa: BLE001
            return False
        sent.append(text)
        return True

    def _nag(self, now: datetime, sent: list[str]) -> None:
        """Hourly task nag: from the owner's start time on the hour, until the open tasks are closed or the day ends (22:xx last)."""
        try:
            got = self.nag(now) if self.nag and self.plugins.enabled("tasks") else None
        except Exception:  # noqa: BLE001
            return
        if not got or not got[1]:
            return
        start, lines = got
        slot = now.replace(minute=start.minute, second=0, microsecond=0)
        if slot > now:
            slot -= timedelta(hours=1)
        key = f"nag:{now.date().isoformat()}:{slot.hour:02d}"
        if slot.hour < start.hour or slot.hour > 22 or now - slot >= timedelta(minutes=30) or key in self.state.data["digests"]:
            return
        if self._deliver(i18n.t("task_nag", n=len(lines), x="\n".join(f"- {t}" for t in lines)), sent):
            self.state.data["digests"][key] = now.isoformat()
            self.state.save()

    def tick(self, now: datetime) -> list[str]:
        """Run everything due at `now`. Returns the messages sent."""
        sent: list[str] = []
        cal = self.plugins.enabled("calendar")
        due = self._due_digests(now) if self.plugins.enabled("digests") else []
        events = self.fetch(now) if cal else []
        messages = self._mail(now) if due and self.mail_fetch else None
        for kind in due:
            if kind == "plan":
                text = plan_digest(events, now) if cal else ""
            else:
                args = (events, now, messages, self.owners, self._tasks())
                text = (morning_digest(*args, self._lists(), calendar=cal) if kind == "morning"
                        else evening_digest(*args, calendar=cal))
            errs = list(getattr(self.fetch, "errors", [])) if cal else []
            if not text.strip():
                self.state.data["digests"][f"{kind}:{now.date().isoformat()}"] = now.isoformat()   # nothing to say: done for today
                self.state.save()
                continue
            if errs:
                text += "\n" + i18n.t("unavailable", names=", ".join(errs))
            if self._deliver(text, sent):
                self.state.data["digests"][f"{kind}:{now.date().isoformat()}"] = now.isoformat()
                self.state.save()          # saved right after each send: a later failure never resends this one
        self._nag(now, sent)
        already = set(self.state.data["reminded"])
        for e in (due_reminders(events, now, self.lead, already, self.allday_at) if self.plugins.enabled("reminders") else []):
            if self._deliver(format_reminder(e, now), sent):
                already.add(reminder_key(e))
                self.state.data["reminded"] = sorted(already)[-500:]
                self.state.save()
        self.state.data["reminded"] = sorted(already)[-500:]
        cutoff = (now.date() - timedelta(days=7)).isoformat()
        self.state.data["digests"] = {k: v for k, v in self.state.data["digests"].items() if k.split(":", 1)[1] >= cutoff}
        self.state.save()
        return sent

    def run_forever(self, clock: Callable[[], datetime], sleep: Callable[[float], None] = _time.sleep,
                    interval: float = 60, max_ticks: int | None = None) -> None:
        n = 0
        while max_ticks is None or n < max_ticks:
            self.tick(clock())
            n += 1
            sleep(interval)
