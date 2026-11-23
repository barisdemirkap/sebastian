import json
import os
import subprocess
import pytest
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from sebastian import nl as nlmod
from sebastian.assistant import Assistant
from sebastian.bot import ApiNotifier, Bot, ConfigurationError, owner_from_env, write_heartbeat
from sebastian.models import Event
from sebastian.notify import StubNotifier
from sebastian.providers import FixtureProvider
from sebastian.scheduler import Scheduler, State
from sebastian.store import Store
from sebastian.telegram import FakeTelegram, TelegramApi
from sebastian.http import LiveAccessDenied

IST = ZoneInfo("Europe/Istanbul")
OWNER = 1000000001
NOW = datetime(2026, 11, 18, 10, 0, tzinfo=IST)
ROOT = Path(__file__).parent.parent


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


def upd(uid, text=None, chat=OWNER, **extra):
    msg = {"chat": {"id": chat}, **({"text": text} if text is not None else {}), **extra}
    return {"update_id": uid, "message": msg}


def make(store=None, hub=None, voice=None, scheduler=None, hb=None, api=None):
    api = api or FakeTelegram()
    store = store or Store()
    clock = Clock()
    a = Assistant(hub or FixtureProvider([]), store, nlmod.NLService("off"), IST, OWNER, clock)
    return Bot(api, a, store, OWNER, clock, voice, scheduler, hb, sleep=lambda s: None), api, store, clock


def test_owner_message_is_answered_logged_and_deduped():
    bot, api, st, _ = make()
    bot.process_update(upd(10, "/yardim"))
    assert len(api.sent) == 1 and api.sent[0][0] == OWNER and api.sent[0][1].startswith("Emrinizdeyim, efendim. Yapabildiklerim")
    assert st.last_update_id() == 10
    bot.process_update(upd(10, "/yardim"))                    # redelivery
    bot.process_update(upd(9, "/yardim"))                     # older
    assert len(api.sent) == 1
    rows = st.messages(OWNER)
    assert [(r["direction"], r["answered"]) for r in rows] == [("in", 1), ("out", 0)]


def test_stranger_is_ignored_silently_without_logging():
    bot, api, st, _ = make()
    bot.process_update(upd(11, "evet", chat=555))
    assert api.sent == [] and st.messages(555) == [] and st.last_update_id() == 11


def test_unanswered_message_is_recovered_once_at_startup():
    st = Store()
    api = FakeTelegram()
    api.fail = True                                           # network down while answering
    bot, _, _, clock = make(st, api=api)
    try:
        bot.process_update(upd(20, "bugün ne var"))
    except RuntimeError:
        pass
    assert [r["answered"] for r in st.messages(OWNER)] == [0]
    api.fail = False
    bot2, _, _, _ = make(st, api=api)
    assert bot2.recover() == 1
    assert api.sent == [(OWNER, "Bugün takvimde bir şey yok.")]
    assert bot2.recover() == 0                                # answered now: never twice


def test_recovery_skips_old_messages_and_stops_after_two_attempts():
    st = Store()
    st.log(OWNER, "eski", "in", NOW - timedelta(hours=3), 1)
    st.log(OWNER, "bugün ne var", "in", NOW - timedelta(minutes=5), 2)
    bot, api, _, _ = make(st)
    assert bot.recover() == 1 and len(api.sent) == 1
    st2 = Store()
    mid = st2.log(OWNER, "bugün ne var", "in", NOW, 3)
    st2.bump_attempt(mid); st2.bump_attempt(mid)
    bot2, api2, _, _ = make(st2)
    assert bot2.recover() == 0 and api2.sent == []


def test_loop_survives_poll_errors_writes_heartbeat_and_ticks_scheduler(tmp_path):
    class Flaky(FakeTelegram):
        n = 0

        def get_updates(self, offset, timeout=0):
            self.n += 1
            if self.n == 1:
                raise OSError("network")
            return super().get_updates(offset, timeout)
    api = Flaky()
    api.inbox = [upd(30, "/yardim")]
    hb = tmp_path / "hb"
    ev = Event("Dis", NOW + timedelta(minutes=20), NOW + timedelta(minutes=80), "p", uid="d")
    st = Store()
    clock = Clock()
    n = ApiNotifier(api, OWNER, st, clock)
    sched = Scheduler(time(8, 30), time(20, 0), lambda now: [ev], n, State(tmp_path / "s.json"))
    bot, _, _, _ = make(st, scheduler=sched, hb=hb, api=api)
    bot.run(poll_timeout=0, max_iters=2)
    texts = [t for _, t in api.sent]
    assert any(t.startswith("Emrinizdeyim, efendim. Yapabildiklerim") for t in texts)
    assert any(t.startswith("Hatırlatma: Dis 20 dk sonra") for t in texts)   # scheduler reminder through the same api
    assert int(hb.read_text()) == int(NOW.timestamp())


def test_scheduler_send_failure_retries_only_the_failed_message(tmp_path):
    ev = [Event("A", NOW + timedelta(minutes=10), NOW + timedelta(minutes=40), "p", uid="a"),
          Event("B", NOW + timedelta(minutes=12), NOW + timedelta(minutes=40), "p", uid="b")]

    class Notifier:
        def __init__(self):
            self.sent, self.fail_on = [], "B"

        def send(self, text):
            if self.fail_on and f" {self.fail_on} " in text:
                raise RuntimeError("down")
            self.sent.append(text)
    n = Notifier()
    s = Scheduler(time(23, 0), time(23, 30), lambda now: ev, n, State(tmp_path / "s.json"))
    assert len(s.tick(NOW)) == 1 and "A" in n.sent[0]
    n.fail_on = None
    out = s.tick(NOW + timedelta(minutes=1))
    assert len(out) == 1 and "B" in out[0]                    # A is not sent twice


def test_retention_purge_runs_once_a_day():
    bot, api, st, clock = make()
    st.log(OWNER, "old", "in", NOW - timedelta(days=40), 1, True)
    bot.step(0)
    assert st.messages(OWNER) == []


def test_heartbeat_is_atomic_epoch(tmp_path):
    p = tmp_path / "x" / "hb"
    write_heartbeat(p, NOW)
    assert p.read_text() == str(int(NOW.timestamp())) and not (tmp_path / "x" / "hb.tmp").exists()
    write_heartbeat(None, NOW)


def test_owner_chat_comes_from_env_only():
    assert owner_from_env({"SEBASTIAN_OWNER_CHAT_ID": " 1000000001 "}) == 1000000001
    for env in ({}, {"SEBASTIAN_OWNER_CHAT_ID": "abc"}):
        with pytest.raises(ConfigurationError):
            owner_from_env(env)
    src = "".join(p.read_text() for p in (ROOT / "src" / "sebastian").glob("*.py"))
    assert "1000000001" not in src                               # never hard-coded


# ---------------------------------------------------------------- voice
class FakeVoice:
    def __init__(self, text="yarın saat 15:00 diş hekimi ekle", miss=()):
        self.text, self.miss, self.spoken = text, list(miss), []

    def missing(self):
        return self.miss

    def transcribe(self, audio):
        return self.text

    def synthesize(self, text):
        self.spoken.append(text)
        return b"OGG"


def test_voice_message_is_echoed_answered_and_spoken():
    v = FakeVoice()
    bot, api, st, _ = make(voice=v)
    api.files["f1"] = b"audio"
    bot.process_update(upd(40, voice={"file_id": "f1", "duration": 4, "file_size": 1000}))
    texts = [t for _, t in api.sent]
    assert texts[0] == "Şöyle anladım: yarın saat 15:00 diş hekimi ekle" and texts[1].startswith("Şunu takvime işliyorum, onaylar mısınız?")
    assert v.spoken and api.voices == [(OWNER, b"OGG")]
    assert st.messages(OWNER)[0]["voice"] == 1
    bot.process_update(upd(41, "evet"))                             # a typed reply stays text-only
    assert len(api.voices) == 1


def test_voice_off_or_tools_missing_says_so_and_never_crashes():
    bot, api, *_ = make(voice=None)
    bot.process_update(upd(50, voice={"file_id": "f"}))
    assert api.sent == [(OWNER, "Sesli mesaj şu an kapalı. Yazarak dener misiniz?")]
    bot, api, *_ = make(voice=FakeVoice(miss=["ffmpeg"]))
    bot.process_update(upd(51, voice={"file_id": "f"}))
    assert api.sent[0][1].startswith("Sesli mesaj şu an kapalı")
    bot, api, *_ = make(voice=FakeVoice(text=None))
    api.files["f"] = b"x"
    bot.process_update(upd(52, voice={"file_id": "f"}))
    assert api.sent == [(OWNER, "Sesi anlayamadım. Yazarak dener misiniz?")]
    bot, api, *_ = make(voice=FakeVoice())
    bot.process_update(upd(53, voice={"file_id": "f", "duration": 999}))
    assert api.sent == [(OWNER, "Ses kaydı çok uzun. Daha kısa gönderir misiniz?")]


def test_voice_module_is_opt_in_and_checks_tools():
    from sebastian import voice
    assert not voice.enabled({}) and voice.enabled({"SEBASTIAN_VOICE": "1"})
    v = voice.Voice(env={}, which=lambda b: None, exists=lambda p: False)
    assert set(v.missing()) == {"ffmpeg", "whisper-cli", "say", "SEBASTIAN_WHISPER_MODEL"}
    calls = []

    class R:
        def __init__(self, rc=0, out=b""):
            self.returncode, self.stdout = rc, out

    def runner(cmd, **kw):
        calls.append(cmd[0])
        return R(0, b"  yarin  saat 3  ")
    v = voice.Voice(env={"SEBASTIAN_WHISPER_MODEL": "/m.bin"}, runner=runner, which=lambda b: "/bin/" + b, exists=lambda p: True)
    assert v.missing() == [] and v.transcribe(b"x") == "yarin saat 3" and calls == ["ffmpeg", "whisper-cli"]
    assert v.transcribe(b"") is None
    assert voice.short_enough("kısa") and not voice.short_enough("x" * 400)


# ---------------------------------------------------------------- telegram client and live guard
def test_telegram_api_uses_injected_transport_and_splits_long_messages():
    calls = []

    def post(url, payload, timeout):
        calls.append((url.split("/")[-1], payload))
        return {"ok": True, "result": [{"update_id": 1}]}
    api = TelegramApi("TOKEN", post=post)
    api.send(OWNER, "x" * 5000)
    assert [c[0] for c in calls] == ["sendMessage", "sendMessage"] and calls[0][1]["chat_id"] == OWNER
    assert api.get_updates(5, 0) == [{"update_id": 1}] and calls[-1][1]["offset"] == 5
    with pytest.raises(ValueError):
        TelegramApi("")
    with pytest.raises(RuntimeError):
        TelegramApi("T", post=lambda *a: {"ok": False}).send(1, "x")


def test_real_telegram_calls_refuse_without_live_flag(monkeypatch):
    monkeypatch.delenv("SEBASTIAN_ALLOW_LIVE", raising=False)
    with pytest.raises(LiveAccessDenied):
        TelegramApi("T").send(1, "hi")


# ---------------------------------------------------------------- watchdog script
def run_watchdog(tmp_path, hb_epoch, now=1_000_000, stale="180"):
    hb = tmp_path / "heartbeat"
    if hb_epoch is not None:
        hb.write_text(str(hb_epoch))
    env = {**os.environ, "SEBASTIAN_HEARTBEAT": str(hb), "SEBASTIAN_WATCHDOG_DRYRUN": "1", "SEBASTIAN_WATCHDOG_NOW": str(now),
           "SEBASTIAN_STALE_SECONDS": stale}
    return subprocess.run(["bash", str(ROOT / "scripts" / "watchdog.sh")], capture_output=True, text=True, env=env)


def test_watchdog_healthy_stale_and_missing(tmp_path):
    ok = run_watchdog(tmp_path, 1_000_000 - 100)
    assert ok.returncode == 0 and ok.stdout == ""
    stale = run_watchdog(tmp_path, 1_000_000 - 500)
    assert stale.returncode == 1 and "DRYRUN: launchctl kickstart -k gui/" in stale.stdout and "com.sebastian.bot" in stale.stdout
    (tmp_path / "heartbeat").unlink()
    assert run_watchdog(tmp_path, None).returncode == 2


def test_deploy_templates_are_templates_only():
    for name in ("com.sebastian.bot.plist.template", "com.sebastian.watchdog.plist.template"):
        t = (ROOT / "deploy" / name).read_text()
        assert "__REPO_DIR__" in t and "TEMPLATE ONLY" in t and "SEBASTIAN_ALLOW_LIVE" not in t.replace("never", "")
    env = (ROOT / "deploy" / "sebastian.env.example").read_text()
    assert "telegram.token" in env and "SEBASTIAN_OWNER_CHAT_ID" in env and "SEBASTIAN_NL_CONSENT" in env
    assert (ROOT / "scripts" / "watchdog.sh").stat().st_mode & 0o111


# ---------------------------------------------------------------- debounce (ADR 0006)
class BurstTelegram(FakeTelegram):
    """First poll returns `first`; the settle poll returns `later`."""

    def __init__(self, first, later):
        super().__init__()
        self.polls = [first, later]

    def get_updates(self, offset, timeout=0):
        return self.polls.pop(0) if self.polls else []


def dated(uid, text, ts, **kw):
    u = upd(uid, text, **kw)
    u["message"]["date"] = ts
    return u


def test_burst_of_texts_is_one_turn():
    ts = int(NOW.timestamp())
    api = BurstTelegram([dated(1, "yarın diş hekimi", ts)], [dated(2, "saat 15:00", ts + 2)])
    bot, _, st, clock = make(api=api)
    bot.now_ts = lambda: ts + 1
    bot.step(0)
    assert [t for _, t in api.sent] == [api.sent[0][1]] and "15:00-16:00" in api.sent[0][1]
    assert [r["answered"] for r in st.messages(OWNER) if r["direction"] == "in"] == [1, 1]
    assert st.last_update_id() == 2


def test_confirm_cancel_commands_never_merge():
    ts = 1_000_000
    from sebastian.bot import group_updates
    ups = [dated(1, "yarın diş hekimi", ts), dated(2, "evet", ts + 1), dated(3, "/bugun", ts + 1), dated(4, "iptal", ts + 1),
           dated(5, "x", ts + 1), dated(6, "y", ts + 2), dated(7, "z", ts + 30)]
    assert [[u["update_id"] for u in g] for g in group_updates(ups, OWNER)] == [[1], [2], [3], [4], [5, 6], [7]]
    voice = dated(8, None, ts)
    voice["message"]["voice"] = {"file_id": "f"}
    assert group_updates([dated(9, "a", ts), voice], OWNER) == [[dated(9, "a", ts)], [voice]]


def test_no_settle_wait_for_old_or_confirm_messages():
    ts = int(NOW.timestamp())
    api = BurstTelegram([dated(1, "evet", ts)], [dated(2, "x", ts)])
    bot, _, st, _ = make(api=api)
    bot.now_ts = lambda: ts
    assert bot.settle(api.polls[0]) == api.polls[0] and len(api.polls) == 2
    old = BurstTelegram([dated(1, "merhaba", ts - 60)], [])
    bot2, *_ = make(api=old)
    bot2.now_ts = lambda: ts
    assert bot2.settle(old.polls[0]) == old.polls[0] and len(old.polls) == 2
