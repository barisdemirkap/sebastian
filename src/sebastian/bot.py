"""The long-running bot (ADR 0005): poll Telegram, answer the owner, tick the scheduler, write a heartbeat.

Reliability: every inbound message is logged before it is handled and the last update_id is stored, so a restart
never handles an update twice, and messages that were received but never answered (crash, outage) are answered at
startup. The message log keeps 30 days. A heartbeat file lets `scripts/watchdog.sh` restart a stuck process."""
from __future__ import annotations
import json
import os
import re
import sys
import time as _time
import urllib.error
from datetime import datetime, time, timedelta
from pathlib import Path

from . import dialogue, i18n, lists as listmod, voice as voice_mod
from .agent import AgentMode
from .assistant import Assistant
from .store import Store

RECOVER_WINDOW = timedelta(hours=2)   # unanswered messages older than this are not answered late
OWNER_ENV = "SEBASTIAN_OWNER_CHAT_ID"
DEBOUNCE_SECONDS = 4                  # texts of the owner this close together are ONE turn (ADR 0006)
SETTLE_WAIT = 2                       # seconds of extra polling after a fresh plain text


BUTTON_MARK = "[button:"                  # an inbound row that is a button tap, e.g. '[button:delete]'
BUTTON_NAMES = {"k": "save", "e": "edit", "x": "cancel", "d": "delete", "a": "list-all-bought", "c": "list-clear", "i": "list-item", "t": "task-done"}
CALLBACK = re.compile(r"([kexdacit]):(\d{1,9}):([0-9a-f]{8})")


class ConfigurationError(RuntimeError):
    pass


def owner_from_env(env=None) -> int:
    env = os.environ if env is None else env
    raw = env.get(OWNER_ENV, "").strip()
    if not raw.lstrip("-").isdigit():
        raise ConfigurationError(f"Set {OWNER_ENV} to the owner's Telegram chat id.")
    return int(raw)


def write_heartbeat(path: Path | None, now: datetime) -> None:
    """Atomic: the watchdog reads the epoch seconds written here."""
    if not path:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(str(int(now.timestamp())))
    os.replace(tmp, path)


class ApiNotifier:
    """Notifier for the scheduler: sends to the owner chat only and records the message in the log."""

    def __init__(self, api, chat_id: int, store: Store, clock):
        self.api, self.chat_id, self.store, self.clock = api, chat_id, store, clock

    def send(self, text: str) -> None:
        self.api.send(self.chat_id, text)
        self.store.log(self.chat_id, text, "out", self.clock())


def plain_text(u: dict, owner: int | None = None) -> str | None:
    """The text of an update that may be merged into a burst: plain text, no command, no confirm or cancel."""
    msg = u.get("message") or {}
    text = msg.get("text")
    if text is None or (owner is not None and (msg.get("chat") or {}).get("id") != owner):
        return None
    if text.lstrip().startswith("/") or dialogue.parse_confirm(text) or dialogue.parse_confirm(text, True):
        return None
    return text


def _mergeable(a: dict, b: dict, owner: int | None) -> bool:
    if plain_text(a, owner) is None or plain_text(b, owner) is None:
        return False
    da, db = (a["message"].get("date"), b["message"].get("date"))
    return isinstance(da, int) and isinstance(db, int) and 0 <= db - da <= DEBOUNCE_SECONDS


def group_updates(updates: list[dict], owner: int | None = None) -> list[list[dict]]:
    """Consecutive mergeable updates grouped in arrival order; everything else (confirm, cancel, commands, voice,
    callbacks, strangers) stands alone."""
    groups: list[list[dict]] = []
    for u in updates:
        if groups and _mergeable(groups[-1][-1], u, owner):
            groups[-1].append(u)
        else:
            groups.append([u])
    return groups


class Bot:
    def __init__(self, api, assistant: Assistant, store: Store, owner: int, clock, voice=None, scheduler=None,
                 heartbeat: Path | None = None, sleep=_time.sleep, settle_wait: float = SETTLE_WAIT, now_ts=_time.time):
        self.api, self.assistant, self.store, self.owner, self.clock = api, assistant, store, owner, clock
        self.voice, self.scheduler, self.heartbeat, self.sleep = voice, scheduler, heartbeat, sleep
        self.settle_wait, self.now_ts = settle_wait, now_ts
        self._purged_on = None

    # -- one message
    def _reply(self, text: str, speak: bool = False, replies: list[str] | None = None, keyboard: dict | None = None) -> None:
        replies = replies or []
        menu = i18n.t("health_menu")
        body = [i for i, r in enumerate(replies) if r != menu]       # the way-out menu never takes the keyboard (ADR 0009)
        for i, r in enumerate(replies):
            if keyboard and body and i == body[-1]:
                self._drop_keyboard()                              # one live keyboard at a time
                mid = self.api.send(self.owner, r, reply_markup=keyboard)
                if mid is not None:
                    self.store.set_kb(self.owner, mid, self.assistant.shown)
            else:
                self.api.send(self.owner, r)
            self.store.log(self.owner, r, "out", self.clock())
        if speak and self.voice is not None and not self.voice.missing():
            for r in [r for r in (replies or []) if voice_mod.short_enough(r)][:voice_mod.SPEAK_MAX_MESSAGES]:
                try:
                    if (ogg := self.voice.synthesize(r)):
                        self.api.send_voice(self.owner, ogg)
                except Exception as exc:  # noqa: BLE001
                    print(f"voice reply error: {type(exc).__name__}", file=sys.stderr)

    def _drop_keyboard(self) -> None:
        kb = self.store.get_kb(self.owner)
        if kb:
            self.store.set_kb(self.owner, None)
            try:
                self.api.remove_keyboard(self.owner, kb[0])
            except Exception as exc:  # noqa: BLE001 - a dead button is rejected as stale anyway
                print(f"keyboard error: {type(exc).__name__}", file=sys.stderr)

    def _sweep_keyboard(self) -> None:
        """After every turn: a keyboard whose preview is no longer the current open one is removed."""
        kb = self.store.get_kb(self.owner)
        cur = self.store.open_pending(self.owner)
        if kb and kb[1] is not None and (cur is None or cur["id"] != kb[1]):     # a list keyboard (no preview) stays; its taps are verified
            self._drop_keyboard()

    def _answer(self, msg_id: int, text: str, from_voice: bool, prefix: list[str] = ()) -> None:
        self.store.bump_attempt(msg_id)
        t0 = self.now_ts()
        replies = self.assistant.handle(self.owner, text, from_voice)
        replies += self.assistant.note_slow(int((self.now_ts() - t0) * 1000))
        kb = self.assistant.keyboard() if replies else None
        self._reply("", False, list(prefix))     # the 'heard' echo is never spoken
        self._reply("", from_voice, replies, kb)
        self._sweep_keyboard()
        self.store.mark_answered(msg_id)

    def process_callback(self, u: dict) -> None:
        """A tap on an inline button (ADR 0007). Never merged into a burst, never answered by the model."""
        uid = u.get("update_id", 0)
        if uid <= self.store.last_update_id():
            return
        self.store.set_last_update_id(uid)
        cq = u.get("callback_query") or {}
        msg = cq.get("message") or {}
        chat = msg.get("chat") or {}
        if (cq.get("from") or {}).get("id") != self.owner or chat.get("id") != self.owner or chat.get("type", "private") != "private":
            return                                    # someone else or another chat: ignored
        m = CALLBACK.fullmatch(cq.get("data") or "")
        cid = cq.get("id", "")
        if not m:
            self._ack(cid, "")
            return
        self.store.log(self.owner, f"{BUTTON_MARK}{BUTTON_NAMES.get(m[1], m[1])}]", "in", self.clock(), uid, answered=True)   # a tap is an inbound message too
        alert, replies = self.assistant.callback(self.owner, m[1], int(m[2]), m[3])
        if not self._ack(cid, alert or "", True) and alert:
            replies = [alert] + replies                # the alert could not be shown as a popup: say it in the chat
        if msg.get("message_id") is not None:
            try:
                self.api.remove_keyboard(self.owner, msg["message_id"])
            except Exception as exc:  # noqa: BLE001
                print(f"keyboard error: {type(exc).__name__}", file=sys.stderr)
            kb = self.store.get_kb(self.owner)
            if kb and kb[0] == msg["message_id"]:
                self.store.set_kb(self.owner, None)
        self._reply("", False, replies, self.assistant.keyboard() if replies else None)
        self._sweep_keyboard()

    def _ack(self, cid: str, text: str, alert: bool = False) -> bool:
        """Answer the tap. True when Telegram accepted it. A stale query ('query is too old', HTTP 400) is expected and
        harmless: the action already ran, so it is not an error."""
        try:
            self.api.answer_callback(cid, text, alert)
            return True
        except urllib.error.HTTPError:
            return False
        except Exception as exc:  # noqa: BLE001
            print(f"callback ack error: {type(exc).__name__}", file=sys.stderr)
            return False

    def _transcribe(self, msg: dict) -> tuple[str | None, list[str]]:
        media = msg.get("voice") or msg.get("audio") or {}
        if self.voice is None:
            return None, [i18n.t("voice_unavailable")]
        if self.voice.missing():
            return None, [i18n.t("voice_unavailable")]
        if (media.get("duration") or 0) > voice_mod.MAX_SECONDS or (media.get("file_size") or 0) > voice_mod.MAX_BYTES:
            return None, [i18n.t("voice_too_long")]
        try:
            text = self.voice.transcribe(self.api.get_file(media["file_id"]))
        except Exception as exc:  # noqa: BLE001
            print(f"voice error: {type(exc).__name__}", file=sys.stderr)
            text = None
        return (text, []) if text else (None, [i18n.t("voice_failed")])

    def process_update(self, u: dict) -> None:
        uid = u.get("update_id", 0)
        if uid <= self.store.last_update_id():
            return                                   # already handled (Telegram redelivery or restart)
        if "callback_query" in u:
            return self.process_callback(u)
        msg = u.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id != self.owner:
            self.store.set_last_update_id(uid)      # strangers: no log, no reply, no processing
            return
        now = self.clock()
        if "text" in msg:
            mid = self.store.log(chat_id, msg["text"], "in", now, uid)
            self.store.set_last_update_id(uid)
            self._answer(mid, msg["text"], False)
        elif "voice" in msg or "audio" in msg:
            self.store.set_last_update_id(uid)
            text, errs = self._transcribe(msg)
            if text is None:
                self._reply("", False, errs)
                return
            mid = self.store.log(chat_id, text, "in", now, uid, voice=True)
            self._answer(mid, text, True, [i18n.t("voice_heard", text=text)])
        else:
            self.store.set_last_update_id(uid)

    def settle(self, updates: list[dict]) -> list[dict]:
        """After a FRESH plain text, poll briefly for the rest of a burst (same offset, so nothing is acknowledged early
        and a crash loses nothing). Old backlog, commands, confirmations and voice are never waited for."""
        for _ in range(2):
            if self.settle_wait <= 0 or not updates:
                break
            last = updates[-1]
            date_ = (last.get("message") or {}).get("date")
            if plain_text(last, self.owner) is None or not isinstance(date_, int) or self.now_ts() - date_ > DEBOUNCE_SECONDS:
                break
            more = self.api.get_updates(self.store.last_update_id() + 1, self.settle_wait)
            seen = {u["update_id"] for u in updates}
            fresh = [u for u in more if u["update_id"] not in seen]
            if not fresh:
                break
            updates = sorted(updates + fresh, key=lambda u: u["update_id"])
        return updates

    def process_group(self, group: list[dict]) -> None:
        if len(group) == 1:
            return self.process_update(group[0])
        fresh = [u for u in group if u.get("update_id", 0) > self.store.last_update_id()]
        if len(fresh) < len(group):               # a redelivered burst is dealt with update by update
            for u in fresh:
                self.process_update(u)
            return
        now = self.clock()
        mids = [self.store.log(self.owner, u["message"]["text"], "in", now, u["update_id"]) for u in group]
        self.store.set_last_update_id(group[-1]["update_id"])
        self._answer(mids[-1], "\n".join(u["message"]["text"] for u in group), False)
        for mid in mids[:-1]:
            self.store.mark_answered(mid)

    # -- startup recovery
    def recover(self) -> int:
        """Answer inbound messages that were logged but never answered (within RECOVER_WINDOW)."""
        n = 0
        for row in self.store.unanswered(self.clock() - RECOVER_WINDOW):
            try:
                self._answer(row["id"], row["text"], bool(row["voice"]))
                n += 1
            except Exception as exc:  # noqa: BLE001
                print(f"recovery error: {type(exc).__name__}", file=sys.stderr)
        return n

    # -- loop
    def _housekeeping(self, now: datetime) -> None:
        if self._purged_on != now.date():
            self.store.purge(now)
            self._purged_on = now.date()

    def step(self, poll_timeout: int = 25) -> None:
        now = self.clock()
        write_heartbeat(self.heartbeat, now)
        self._housekeeping(now)
        try:
            updates = self.settle(self.api.get_updates(self.store.last_update_id() + 1, poll_timeout))
            for group in group_updates(updates, self.owner):
                try:
                    self.process_group(group)
                except Exception as exc:  # noqa: BLE001 - one bad update never stops the bot
                    print(f"update error: {type(exc).__name__}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - network blips are expected
            print(f"poll error: {type(exc).__name__}", file=sys.stderr)
            self.sleep(5)
        if self.scheduler is not None:
            try:
                self.scheduler.tick(self.clock())
            except Exception as exc:  # noqa: BLE001
                print(f"scheduler error: {type(exc).__name__}", file=sys.stderr)
        self._retry()
        write_heartbeat(self.heartbeat, self.clock())

    def _retry(self) -> None:
        """ADR 0021: queued messages are read again once the model is back; each preview goes out alone and needs its own yes."""
        try:
            replies = self.assistant.retry_tick(self.clock())
            if replies:
                self._reply("", False, replies, self.assistant.keyboard())
                self._sweep_keyboard()
        except Exception as exc:  # noqa: BLE001 - the queue keeps the message; never stops the loop
            print(f"retry error: {type(exc).__name__}", file=sys.stderr)

    def run(self, poll_timeout: int = 25, max_iters: int | None = None) -> None:
        self.recover()
        i = 0
        while max_iters is None or i < max_iters:
            i += 1
            self.step(poll_timeout)


def task_nag_state(store: Store, owner: int, now: datetime):
    """(start time, open task titles that are due) while today's hourly task nag is on, else None."""
    try:
        st = json.loads(store.get_meta("task_nag", "") or "{}")
        if st.get("date") != now.date().isoformat():
            return None
        after = json.loads(store.get_meta("task_after", "{}") or "{}")
        hh, mm = map(int, st["at"].split(":"))
        lines = [r["title"] for r in store.open_tasks(owner) if after.get(str(r["id"]), "00:00") <= now.strftime("%H:%M")
                 and (not r["due"] or r["due"] <= now.date().isoformat())]
        return time(hh, mm), lines
    except Exception:  # noqa: BLE001
        return None


def build_bot(cfg, env, api, hub, fetcher, mail_fetch=None, clock=None, store: Store | None = None) -> Bot:
    """Wire the pieces. Every external edge (api, hub, fetcher) is injected, so this is testable offline."""
    from zoneinfo import ZoneInfo
    from . import assist as assistmod, nl as nlmod
    from .service import build_scheduler
    tz = ZoneInfo(cfg.timezone)
    clock = clock or (lambda: datetime.now(tz))
    owner = owner_from_env(env)
    store = store or Store(cfg.db_path)
    assistant = Assistant(hub, store, nlmod.NLService.from_env(env), tz, owner, clock, cfg.reminder_lead_minutes,
                          readonly=env.get("SEBASTIAN_NL", "").strip().lower() == "readonly", plugins=cfg.plugins)
    assistant.assist = assistmod.AssistService.from_env(env, assistant.nl)      # ADR 0023: shadow by default, off without the consent
    assistant.agent = AgentMode.from_env(assistant, env)       # None unless SEBASTIAN_NL=agent and the owner's consent (ADR 0019)
    voice = voice_mod.Voice(env) if voice_mod.enabled(env) else None
    scheduler = build_scheduler(cfg, fetcher, ApiNotifier(api, owner, store, clock), mail_fetch=mail_fetch,
                               tasks=lambda: [r["title"] for r in store.open_tasks(owner)],
                               lists=lambda: listmod.digest_rows(store, owner),
                               nag=lambda now: task_nag_state(store, owner, now))
    return Bot(api, assistant, store, owner, clock, voice, scheduler, cfg.heartbeat_path)
