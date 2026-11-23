"""ADR 0021: simple mode when the language model is unavailable, the retry queue and its replay."""
import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from sebastian import health, i18n, nl as nlmod, rules
from sebastian.assistant import PROBE_GAP, Assistant
from sebastian.providers import FixtureProvider
from sebastian.store import Store

IST = ZoneInfo("Europe/Istanbul")
OWNER = 4242
NOW = datetime(2026, 11, 19, 7, 2, tzinfo=IST)
TODAY = date(2026, 11, 19)
J = "copu evden cikarken cikartacagim bugun 10da initech toplantim var 11de de cto ile toplantim var"


class Model:
    name, model = "m", "m-1"

    def __init__(self, up=False, reason="usage", reset=None):
        self.up, self.reason, self.reset, self.calls = up, reason, reset, 0

    def parse(self, message, context):
        self.calls += 1
        if not self.up:
            raise nlmod.ProviderError("down", reason=self.reason, reset=self.reset)
        if "initech" in message:
            return {"intent": "add_event", "title": "GTech toplantısı", "date": "2026-11-19", "time": "10:00"}
        if "berber" in message:
            return {"intent": "add_event", "title": "Berber", "date": "2026-11-20", "time": "09:00"}
        return {"intent": "chitchat"}


def make(model=None):
    model = model or Model()
    now = [NOW]
    st = Store()
    nl = nlmod.NLService("claude", {"claude": model, "qwen": Model(reason=model.reason)}, consent=True)
    a = Assistant(FixtureProvider([]), st, nl, IST, OWNER, lambda: now[0])
    return a, st, model, now


def say(a, text):
    return a.handle(OWNER, text)


@pytest.fixture(autouse=True)
def _pin():
    i18n.pin("efendim")
    yield
    i18n.pin(None)


# -- the rules only accept ONE high-confidence thing
@pytest.mark.parametrize("text", [J, "yarın sabah 9da berber cuma 5te dişçi var", "bugün 10da toplantım var 11de toplantım var",
                                  "yarın 15:00 diş hekimi ve 16:00 berber", "bugün toplantım var yarın dişçim var",
                                  "yarın 15:00 diş hekimi\ncuma 10:00 berber", "yarın dişçi; cuma 10:00 berber",
                                  "bugün 10da toplantım var bir de akşam yemeği hazırlanacak mutfakta çok iş var yarın",
                                  "asdf qwer", "çöp"])
def test_complex_or_unclear_is_not_safe(text):
    assert rules.safe(text, TODAY) is None


@pytest.mark.parametrize("text,title,time", [("yarın 15:00 diş hekimi", "Diş hekimi", "15:00"),
                                             ("bugün 10da toplantım var", "Toplantım", "10:00"),
                                             ("yarın 15:00 ve diş hekimi", "Diş hekimi", "15:00")])
def test_single_event_title_is_the_subject_phrase_only(text, title, time):
    p = rules.safe(text, TODAY)
    assert p["intent"] == "add_event" and p["title"] == title and p["time"] == time


def test_reads_and_commands_stay_deterministic():
    assert rules.safe("bugün ne var", TODAY)["intent"] == "list_events"
    assert rules.safe("demo toplantıyı sil", TODAY)["intent"] == "delete_event"
    assert rules.safe("merhaba", TODAY)["intent"] == "chitchat"


# -- outage behaviour
def test_complex_message_is_queued_with_one_notice_and_no_menu():
    a, st, m, now = make()
    out = say(a, J)
    assert len(out) == 2 and "basit kipteyim" in out[0] and "kullanım sınırı" in out[0]
    assert out[1].startswith("Mesajınızı kaydettim, hiçbir şey eklemedim; model döndüğünde yeniden okuyup önünüze getireceğim")
    assert a.store.open_pending(OWNER) is None and [r["text"] for r in st.queue_list(OWNER)] == [J]
    assert not any(i18n.t("health_menu") == r for r in out)


def test_notice_once_per_outage_and_the_same_message_is_stored_once():
    a, st, m, now = make()
    say(a, J)
    now[0] += timedelta(seconds=20)
    again = say(a, J)
    other = say(a, "yarın sabah 9da berber cuma 5te dişçi var")
    assert len(again) == 1 and len(other) == 1 and "basit kip" not in again[0] + other[0]
    assert "Bekleyen: 1" in again[0] and "Bekleyen: 2" in other[0] and len(st.queue_list(OWNER)) == 2


def test_no_way_out_menu_during_an_outage_even_with_frustration_and_signals():
    a, st, m, now = make()
    for i in range(6):
        now[0] += timedelta(seconds=30)
        out = say(a, f"ulan anlamıyorsun yarın {i}da berber cuma 5te dişçi var")
        assert i18n.t("health_menu") not in out
    assert health.signals_since(st.c, OWNER, NOW - timedelta(minutes=1)).count("S5") == 1   # once per outage


def test_notice_gives_the_reason_and_the_expected_recovery():
    reset = int(datetime(2026, 11, 19, 12, 30, tzinfo=IST).timestamp())
    a, st, m, now = make(Model(reason="usage", reset=reset))
    assert "12:30" in say(a, J)[0]
    b, *_ = make(Model(reason="network"))
    n = say(b, J)[0]
    assert "bağlantı sorunu" in n and "Tahminen" not in n


def test_simple_message_is_previewed_by_the_rules_during_an_outage():
    a, st, m, now = make()
    out = say(a, "yarın 15:00 diş hekimi")
    assert "basit kipteyim" in out[0] and "Diş hekimi" in out[-1] and a.store.open_pending(OWNER)
    assert st.queue_list(OWNER) == []


def test_the_model_is_not_called_for_every_message_during_an_outage():
    a, st, m, now = make()
    say(a, J)
    calls = m.calls
    now[0] += timedelta(seconds=30)
    say(a, "yarın sabah 9da berber cuma 5te dişçi var")
    assert m.calls == calls                      # within PROBE_GAP: no new wait
    now[0] += PROBE_GAP
    say(a, "yarın sabah 9da berber cuma 5te dişçi var")
    assert m.calls > calls


def test_no_outage_without_a_model_keeps_the_plain_rules():
    st = Store()
    a = Assistant(FixtureProvider([]), st, nlmod.NLService("off"), IST, OWNER, lambda: NOW)
    assert "basit kip" not in "\n".join(say(a, "yarın 15:00 diş hekimi")) and st.queue_list(OWNER) == []


# -- recovery and replay
def test_replay_is_oldest_first_one_at_a_time_and_writes_nothing():
    a, st, m, now = make()
    say(a, J)
    say(a, "yarın sabah 9da berber cuma 5te dişçi var")
    assert a.retry_tick(now[0] + timedelta(seconds=30)) == [] and len(st.queue_list(OWNER)) == 2   # still down, within the gap
    m.up = True
    now[0] += PROBE_GAP + timedelta(seconds=1)
    out = a.retry_tick(now[0])
    assert out[0].startswith("Efendim, daha önce ilettiğiniz şu mesajı şimdi okudum:") and "initech" in out[0]
    assert "GTech toplantısı" in out[-1] and a.store.open_pending(OWNER)
    assert [r["text"] for r in st.queue_list(OWNER)][0].startswith("yarın sabah")
    assert a.retry_tick(now[0]) == []                       # the open preview is answered first
    assert FixtureProvider([]).events(NOW, NOW + timedelta(days=3)) == []
    say(a, "hayır")
    nxt = a.retry_tick(now[0])
    assert "Berber" in nxt[-1] and st.queue_list(OWNER) == [r for r in st.queue_list(OWNER)]
    say(a, "hayır")
    assert a.retry_tick(now[0]) == [] and st.queue_list(OWNER) == []


def test_replay_keeps_the_message_while_the_model_is_still_down():
    a, st, m, now = make()
    say(a, J)
    now[0] += PROBE_GAP + timedelta(seconds=1)
    assert a.retry_tick(now[0]) == [] and len(st.queue_list(OWNER)) == 1
    assert a.store.open_pending(OWNER) is None


def test_the_outage_ends_with_the_next_successful_call_and_a_new_outage_is_told_again():
    a, st, m, now = make()
    say(a, J)
    m.up = True
    now[0] += PROBE_GAP + timedelta(seconds=1)
    say(a, "merhaba")
    assert not a._outage_active()
    m.up = False
    now[0] += timedelta(minutes=10)
    assert "basit kipteyim" in say(a, J + " x")[0]


# -- /bekleyenler
def test_bekleyenler_lists_and_clears():
    a, st, m, now = make()
    assert say(a, "/bekleyenler") == [i18n.t("queue_empty")]
    say(a, J)
    listing = say(a, "/bekleyenler")[0]
    assert "1. " + J in listing and "/bekleyenler temizle" in listing
    assert say(a, "/bekleyenler temizle") == [i18n.t("queue_cleared", n=1)] and st.queue_list(OWNER) == []


def test_help_texts_mention_simple_mode_and_the_queue():
    assert "/bekleyenler" in i18n.t("commands_full") and "basit kip" in i18n.t("guide") and "/bekleyenler" in i18n.t("guide")


# -- store
def test_queue_is_idempotent_and_survives_a_migration_with_a_backup(tmp_path):
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE agent_grants (id INTEGER PRIMARY KEY, chat_id INTEGER, pending_id INTEGER, args_hash TEXT, created_at TEXT, used INTEGER)")
    c.execute("CREATE TABLE list_items (id INTEGER PRIMARY KEY, chat_id INTEGER, list_key TEXT, text TEXT, status TEXT, created_at TEXT, closed_at TEXT)")
    c.commit()
    c.close()
    st = Store(p)
    bak = tmp_path / "old.db.pre-retry.bak"
    assert bak.exists() and oct(os.stat(bak).st_mode & 0o777) == "0o600"
    assert "retry_queue" not in {r[0] for r in sqlite3.connect(bak).execute("SELECT name FROM sqlite_master")}
    assert st.queue_add(1, "x", NOW) == st.queue_add(1, "x", NOW) and len(st.queue_list(1)) == 1
    st.c.close()
    m = bak.stat().st_mtime_ns
    st2 = Store(p)                                              # second open: no new backup, data intact
    assert bak.stat().st_mtime_ns == m and len(st2.queue_list(1)) == 1


# -- the provider reports why it failed
def _run(out, rc=0):
    return lambda cmd, inp, cwd, env, timeout: (rc, out)


def test_claude_provider_reports_usage_limit_with_reset():
    prov = nlmod.ClaudeCodeProvider(runner=_run(json.dumps({"is_error": True, "result": "Claude AI usage limit reached|1763524200"}), 1))
    with pytest.raises(nlmod.ProviderError) as e:
        prov.parse("x", {})
    assert e.value.reason == "usage" and e.value.reset == 1763524200


def test_claude_provider_reports_network_for_a_timeout():
    import subprocess

    def boom(*a):
        raise subprocess.TimeoutExpired("claude", 1)
    with pytest.raises(nlmod.ProviderError) as e:
        nlmod.ClaudeCodeProvider(runner=boom).parse("x", {})
    assert e.value.reason == "network"
