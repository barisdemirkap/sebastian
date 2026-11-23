import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest
from sebastian import cli
from sebastian import config as cfg
from sebastian.assistant import Assistant
from sebastian.bot import build_bot
from sebastian.importer import load_plan, parse_reminder
from sebastian import nl as nlmod
from sebastian.providers import FixtureProvider
from sebastian.store import Store
from sebastian.telegram import FakeTelegram

IST = ZoneInfo("Europe/Istanbul")
ROOT = Path(__file__).parent.parent
TODAY = date(2026, 11, 18)
OWNER = 1000000001


def plan(tmp_path, items):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(items))
    return p


def test_sample_plan_loads_cleanly():
    r = load_plan(ROOT / "examples" / "plan-sample.json", IST, TODAY)
    assert r.problems == [] and len(r.drafts) == 7
    d = {x.title: x for x in r.drafts}
    assert d["Doktor randevusu"].reminder_minutes == 60 and d["Doktor randevusu"].end - d["Doktor randevusu"].start == timedelta(minutes=45)
    assert d["Alışveriş listesini hazırla"].all_day and d["Çamaşır yıkama"].start == datetime(2026, 11, 18, 18, 0, tzinfo=IST)


def test_bad_items_are_reported_not_imported(tmp_path):
    p = plan(tmp_path, [{"date": "2026-11-19", "title": "ok"}, {"date": "2026-11-01", "title": "past"},
                        {"date": "2026-11-19", "time": "25:00", "title": "x"}, {"date": "bad", "title": "x"},
                        {"date": "2026-11-19"}, {"date": "2026-11-19", "title": "r", "reminder": "soon"}, "str"])
    r = load_plan(p, IST, TODAY)
    assert [d.title for d in r.drafts] == ["ok"] and len(r.problems) == 6
    with pytest.raises(ValueError):
        load_plan(plan(tmp_path, {"nope": 1}), IST, TODAY)
    with pytest.raises(ValueError):
        load_plan(tmp_path / "missing.json", IST, TODAY)


def test_reminder_formats():
    assert [parse_reminder(x) for x in (None, 30, "45m", "1h", "1d", "20")] == [None, 30, 45, 60, 1440, 20]
    for bad in ("x", True, -5, 99999):
        with pytest.raises(ValueError):
            parse_reminder(bad)


def write_cfg(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(f'timezone = "Europe/Istanbul"\ndb_path = "{tmp_path / "a.db"}"\nheartbeat_path = "{tmp_path / "hb"}"\n'
                 f'state_path = "{tmp_path / "state.json"}"\n')
    return p


def test_import_plan_cli_stores_one_pending_and_bot_confirms_it(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SEBASTIAN_OWNER_CHAT_ID", str(OWNER))
    c = write_cfg(tmp_path)
    p = plan(tmp_path, [{"date": "2099-01-05", "time": "10:00", "title": "Berber"}, {"date": "2099-01-06", "title": "Fatura"}])
    cli.main(["--config", str(c), "import-plan", str(p)])
    out = capsys.readouterr().out
    assert "Plan dosyasından 2 kayıt hazırlandı. Onaylarsanız takvime işlerim." in out
    assert "Şu 2 etkinliği takvime işliyorum, onaylar mısınız?" in out and "- Berber, Pazartesi 5 Ocak 2099 10:00-11:00" in out and "(evet / hayır)" in out
    st = Store(tmp_path / "a.db")
    row = st.open_pending(OWNER)
    assert row is not None and row["intent"] == "add_event"
    # the bot (same database) executes the STORED action on 'evet'; before that the calendar is untouched
    hub = FixtureProvider([])
    now = datetime(2098, 12, 30, 9, 0, tzinfo=IST)
    a = Assistant(hub, st, nlmod.NLService("off"), IST, OWNER, lambda: now - timedelta(days=0))
    assert hub.events(datetime(2099, 1, 1, tzinfo=IST), datetime(2099, 2, 1, tzinfo=IST)) == []
    row_now = datetime.fromisoformat(row["created_at"])
    a.clock = lambda: row_now + timedelta(minutes=5)
    assert a.handle(OWNER, "evet") == ["Emredersiniz, efendim. 2 etkinlik ekledim."]
    assert len(hub.events(datetime(2099, 1, 1, tzinfo=IST), datetime(2099, 2, 1, tzinfo=IST))) == 2


def test_import_plan_requires_owner_env_and_valid_items(tmp_path, monkeypatch):
    monkeypatch.delenv("SEBASTIAN_OWNER_CHAT_ID", raising=False)
    c = write_cfg(tmp_path)
    p = plan(tmp_path, [{"date": "2099-01-05", "title": "x"}])
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", str(c), "import-plan", str(p)])
    assert "SEBASTIAN_OWNER_CHAT_ID" in str(e.value)
    monkeypatch.setenv("SEBASTIAN_OWNER_CHAT_ID", str(OWNER))
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", str(c), "import-plan", str(plan(tmp_path, [{"date": "2001-01-01", "title": "old"}]))])
    assert "Nothing to import" in str(e.value)


def test_import_send_needs_live_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("SEBASTIAN_OWNER_CHAT_ID", str(OWNER))
    monkeypatch.delenv("SEBASTIAN_ALLOW_LIVE", raising=False)
    c = tmp_path / "c.toml"
    c.write_text(f'timezone = "UTC"\ndb_path = "{tmp_path / "a.db"}"\n[[accounts]]\nprovider="google"\naddress="a@b.c"\nlabel="p"\nenabled=true\n')
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", str(c), "import-plan", "--send", str(plan(tmp_path, [{"date": "2099-01-05", "title": "x"}]))])
    assert "disabled" in str(e.value)


def test_parse_redirect_accepts_code_or_full_url():
    assert cli.parse_redirect("abc", "s1") == ("abc", "s1")
    assert cli.parse_redirect("http://127.0.0.1:8765/callback?state=S&code=4%2FXYZ&scope=x", None) == ("4/XYZ", "S")


def test_chat_command_runs_offline(tmp_path, capsys, monkeypatch):
    c = write_cfg(tmp_path)
    lines = iter(["/yardim", "bugün ne var"])

    def fake_input(prompt=""):
        try:
            return next(lines)
        except StopIteration:
            raise EOFError
    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.delenv("SEBASTIAN_NL", raising=False)
    cli.main(["--config", str(c), "chat"])
    out = capsys.readouterr().out
    assert "Yapabildiklerim:" in out and "Standup" in out


def test_build_bot_wires_owner_nl_and_voice_from_env(tmp_path):
    conf = cfg.parse({"timezone": "Europe/Istanbul", "db_path": str(tmp_path / "x.db"), "heartbeat_path": str(tmp_path / "hb")})
    env = {"SEBASTIAN_OWNER_CHAT_ID": str(OWNER), "SEBASTIAN_NL": "claude", "SEBASTIAN_NL_CONSENT": "1", "SEBASTIAN_VOICE": "1"}
    bot = build_bot(conf, env, FakeTelegram(), FixtureProvider([]), lambda now: [])
    assert bot.owner == OWNER and bot.assistant.nl.enabled and bot.voice is not None
    assert bot.scheduler.plan.hour == 22 and bot.heartbeat == tmp_path / "hb"
    bot2 = build_bot(conf, {"SEBASTIAN_OWNER_CHAT_ID": str(OWNER)}, FakeTelegram(), FixtureProvider([]), lambda now: [])
    assert not bot2.assistant.nl.enabled and bot2.voice is None
