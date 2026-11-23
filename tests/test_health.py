"""ADR 0009: conversation health monitor. Text-free signals S1-S6 + REPORT, way-out menu, /bildir, /saglik, purge."""
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sebastian import health, i18n, nl as nlmod
from sebastian.assistant import Assistant
from sebastian.bot import Bot
from sebastian.providers import FixtureProvider
from sebastian.store import Store
from sebastian.telegram import FakeTelegram
from test_assistant import make, say, FakeProv, nl_with, Clock, IST, NOW, OWNER
from test_bot import upd

MENU = i18n.t("health_menu")
ROOT = Path(__file__).parent.parent


def turn(a, st, text):
    """One owner turn the way the bot loop does it: handle, then log what was sent."""
    out = a.handle(OWNER, text)
    for r in out:
        st.log(OWNER, r, "out", a.clock())
    return out


def signals(st, since=None):
    return health.signals_since(st.c, OWNER, since or NOW - timedelta(days=1))


# ---- store -----------------------------------------------------------------------------------------------------
def test_health_table_migration_is_idempotent_and_keeps_data(tmp_path):
    db = tmp_path / "old.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    c.execute("INSERT INTO meta VALUES ('k','v')")
    c.commit()
    c.close()
    s = Store(db)
    health.record(s.c, OWNER, "S4", "x", NOW)
    Store(db)                                              # second open: nothing breaks, nothing lost
    s2 = Store(db)
    assert health.signals_since(s2.c, OWNER, NOW - timedelta(hours=1)) == ["S4"]
    assert s2.c.execute("SELECT value FROM meta WHERE key='k'").fetchone()[0] == "v"


def test_purge_drops_events_older_than_30_days_with_the_message_log():
    st = Store()
    health.record(st.c, OWNER, "S1", "old", NOW - timedelta(days=31))
    health.record(st.c, OWNER, "S1", "new", NOW - timedelta(days=2))
    st.purge(NOW)
    assert st.c.execute("SELECT detail FROM health_events").fetchall()[0][0] == "new"
    assert st.c.execute("SELECT count(*) FROM health_events").fetchone()[0] == 1


def test_counts_and_format():
    st = Store()
    for sig in ("S1", "S1", "S4"):
        health.record(st.c, OWNER, sig, "", NOW)
    c = health.counts(st.c, NOW - timedelta(hours=1))
    assert health.format_counts(c[OWNER]) == "S1=2, S4=1"


# ---- classification --------------------------------------------------------------------------------------------
def test_unclear_and_frustration_detection():
    assert health.is_unclear(i18n.t("nl_unsure")) and health.is_unclear(i18n.t("unclear_examples"))
    assert health.is_unclear(i18n.t("pending_unclear", reoffer=i18n.t("pending_reoffer")))
    assert health.is_unclear(i18n.REPEAT_LEADS[1] + i18n.t("nl_unsure"))
    assert not health.is_unclear(i18n.t("cancelled"))
    for text in ("Anlamıyorsun!", "ulan", "DEDİM YA", "zaten yazdım"):
        assert health.is_frustrated(text)
    assert not health.is_frustrated("yarın 15:00 toplantı")


# ---- signals through the assistant ----------------------------------------------------------------------------
def test_s4_frustration_adds_one_way_out_menu_then_cooldown_and_keeps_the_preview():
    a, hub, st, clock = make()
    turn(a, st, "yarın saat 15:00 diş hekimi ekle")
    first = turn(a, st, "ulan")
    assert first[-1] == MENU and st.open_pending(OWNER) is not None
    second = turn(a, st, "dedim ya")
    assert MENU not in second                                   # cooldown: one menu per 30 minutes
    clock.now = NOW + timedelta(minutes=31)
    assert turn(a, st, "olmuyor")[-1] == MENU
    assert signals(st).count("S4") == 3


def test_s2_three_unclear_replies_in_a_row():
    a, hub, st, _ = make()
    outs = [turn(a, st, j) for j in ("qqq", "www", "eee")]
    assert MENU not in outs[1] and outs[2][-1] == MENU
    assert signals(st).count("S2") == 1


def test_s1_repeat_guard_is_counted():
    a, hub, st, _ = make()
    turn(a, st, "qqq")
    turn(a, st, "qqq")
    assert "S1" in signals(st)


def test_s3_expired_preview():
    a, hub, st, clock = make()
    turn(a, st, "yarın saat 15:00 diş hekimi ekle")
    clock.now = NOW + timedelta(minutes=11)
    turn(a, st, "evet")
    assert "S3" in signals(st, NOW - timedelta(days=1))


def test_two_signals_inside_the_window_make_an_incident():
    a, hub, st, _ = make()
    turn(a, st, "qqq")
    out = turn(a, st, "qqq")                                    # S1 only: one signal, no menu
    assert MENU not in out
    health.record(st.c, OWNER, "S5", "", a.clock())
    assert MENU in turn(a, st, "qqq")                           # S1 again + the earlier S5


def test_s5_model_chain_failure_is_counted():
    a, hub, st, _ = make(nl=nl_with(FakeProv(exc=RuntimeError("down"))))
    turn(a, st, "yarın saat 15:00 diş hekimi ekle")
    assert "S5" in signals(st)


def test_s6_slow_update_through_the_bot():
    api, st, clock = FakeTelegram(), Store(), Clock()
    a = Assistant(FixtureProvider([]), st, nlmod.NLService("off"), IST, OWNER, clock)
    ticks = iter([0, 9.0, 9.0, 9.0, 9.0, 9.0])
    bot = Bot(api, a, st, OWNER, clock, sleep=lambda s: None, now_ts=lambda: next(ticks))
    bot.process_update(upd(1, "/yardim"))
    assert "S6" in signals(st)


def test_no_way_out_menu_right_after_a_preview_even_when_the_update_was_slow():
    """Incident: a slow update (S6) with an earlier S3 made the menu follow a perfectly good preview."""
    api, st, clock = FakeTelegram(), Store(), Clock()
    a = Assistant(FixtureProvider([]), st, nlmod.NLService("off"), IST, OWNER, clock)
    health.record(st.c, OWNER, "S3", "preview-expired", clock())
    ticks = iter([0, 9.0, 9.0, 9.0, 9.0, 9.0])
    bot = Bot(api, a, st, OWNER, clock, sleep=lambda s: None, now_ts=lambda: next(ticks))
    bot.process_update(upd(1, "yarın saat 15:00 diş hekimi ekle"))
    sent = [t for _, t in api.sent]
    assert any("onaylar mısınız" in t for t in sent) and MENU not in sent
    assert "S6" in signals(st)


def test_repeated_read_only_query_is_never_a_failure():
    a, hub, st, _ = make()
    outs = ["\n".join(turn(a, st, "bugün ne var")) for _ in range(4)]
    assert all(MENU not in o and "aynı" not in o.lower() for o in outs)
    assert "S1" not in signals(st)


def test_no_message_text_is_ever_stored():
    a, hub, st, _ = make()
    turn(a, st, "ulan zqsecret kelime")
    turn(a, st, "/bildir")
    dump = " ".join(str(tuple(r)) for r in st.c.execute("SELECT * FROM health_events"))
    assert "zqsecret" not in dump and "ulan" not in dump


# ---- /bildir and /saglik ---------------------------------------------------------------------------------------
def test_bildir_logs_acknowledges_once():
    a, hub, st, clock = make()
    assert turn(a, st, "/bildir") == [i18n.t("report_thanks")]
    assert signals(st) == ["REPORT"]
    assert turn(a, st, "/bildir") == [i18n.t("report_again")]
    assert signals(st) == ["REPORT"]
    clock.now = NOW + timedelta(minutes=11)
    assert turn(a, st, "/bildir") == [i18n.t("report_thanks")]


def test_saglik_shows_24h_counts_and_nothing_older():
    a, hub, st, _ = make()
    assert turn(a, st, "/saglik") == [i18n.t("health_none")]
    health.record(st.c, OWNER, "S4", "", NOW - timedelta(hours=30))
    assert turn(a, st, "/saglik") == [i18n.t("health_none")]
    health.record(st.c, OWNER, "S4", "", NOW - timedelta(hours=2))
    health.record(st.c, OWNER, "S1", "", NOW - timedelta(hours=1))
    assert turn(a, st, "/saglik") == [i18n.t("health_title", rows="S1=1, S4=1")]


def test_stranger_leaves_no_health_event():
    a, hub, st, _ = make()
    assert a.handle(999, "ulan") == [] and a.handle(999, "/bildir") == []
    assert st.c.execute("SELECT count(*) FROM health_events").fetchone()[0] == 0


def test_commands_are_listed():
    assert "/bildir" in i18n.t("commands_full") and "/saglik" in i18n.t("commands_full")


# ---- the menu never takes the preview's keyboard ---------------------------------------------------------------
def test_menu_does_not_take_the_keyboard():
    st, api, clock = Store(), FakeTelegram(), Clock()
    a = Assistant(FixtureProvider([]), st, nlmod.NLService("off"), IST, OWNER, clock)
    bot = Bot(api, a, st, OWNER, clock, sleep=lambda s: None)
    bot.process_update(upd(1, "yarın saat 15:00 diş hekimi ekle"))
    assert len(api.markups) == 1
    bot.process_update(upd(2, "ulan"))
    assert api.sent[-1][1] == MENU
    assert len(api.markups) == 1                                # the menu carries no keyboard
    assert st.get_kb(OWNER) is not None                         # the keyboard stays on the preview


# ---- scripts/health_report.py ---------------------------------------------------------------------------------
def test_health_report_script(tmp_path):
    db = tmp_path / "h.db"
    s = Store(db)
    now = datetime.now()
    health.record(s.c, OWNER, "S4", "", now)
    health.record(s.c, OWNER, "S2", "", now)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "health_report.py"), "--db", str(db)], capture_output=True, text=True)
    assert r.returncode == 0 and "S2=1, S4=1" in r.stdout and "frustrated or stuck" in r.stdout
    empty = subprocess.run([sys.executable, str(ROOT / "scripts" / "health_report.py"), "--db", str(tmp_path / "none.db")],
                           capture_output=True, text=True)
    assert empty.returncode == 1
