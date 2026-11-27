"""ADR 0005: plugin registry. Everything on by default; a switched-off plugin refuses with a friendly message and drops out of
the digests; enabled plugins still write only through preview + confirm. Offline."""
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from test_assistant import make
from sebastian import config as cfg, i18n
from sebastian.agent_tools import AgentTools, TOOL_NAMES
from sebastian.assistant import Assistant
from sebastian.digest import evening_digest, morning_digest, plan_digest
from sebastian.models import Event
from sebastian.notify import StubNotifier
from sebastian.plugins import NAMES, REGISTRY, Plugins, PluginConfigError
from sebastian.scheduler import Scheduler, State

IST = ZoneInfo("Europe/Istanbul")


def ev(title="Dis", day=19, hour=10):
    s = datetime(2026, 11, day, hour, 0, tzinfo=IST)
    return Event(title, s, s.replace(hour=hour + 1), "p", uid=title)


def asst(**off):
    a, hub, st, clock = make()
    a.plugins = Plugins(frozenset(k for k, v in off.items() if not v))
    return a, st


def say(a, text):
    return "\n".join(a.handle(a.owner, text))


def test_config_defaults_all_on_and_parses_switches():
    assert cfg.parse({}).plugins.disabled == frozenset()
    assert all(on for _, on in cfg.parse({}).plugins.summary())
    p = cfg.parse({"plugins": {"lists": False, "tasks": True}}).plugins
    assert not p.enabled("lists") and p.enabled("tasks")


def test_config_rejects_unknown_plugin_and_non_bool():
    with pytest.raises(cfg.ConfigError, match="unknown plugin"):
        cfg.parse({"plugins": {"mail": False}})
    with pytest.raises(cfg.ConfigError, match="true or false"):
        cfg.parse({"plugins": {"tasks": "no"}})
    with pytest.raises(PluginConfigError):
        Plugins.from_raw({"calendar": 0})


def test_contract_names_are_known_and_tools_are_owned_by_registered_plugins():
    for p in REGISTRY.values():
        assert all(r in REGISTRY for r in p.requires)
    owned = {t for p in REGISTRY.values() for t in p.tools}
    assert owned <= set(TOOL_NAMES)
    assert {Plugins().owner_of("tools", t) for t in TOOL_NAMES} - {None} <= set(NAMES)
    assert {"calendar", "tasks", "lists", "reminders", "digests"} == set(NAMES)


def test_reminders_depend_on_calendar():
    p = Plugins(frozenset({"calendar"}))
    assert not p.enabled("reminders") and p.off_name("reminders") == "calendar"


def test_default_all_on_behaves_as_before():
    a, st = asst()
    assert "Alınacaklar" in say(a, "/liste") or "liste" in say(a, "/liste").lower()
    assert "özelliği ayarlarda kapalı" not in say(a, "/gorevler")


@pytest.mark.parametrize("plugin,cmd", [("tasks", "/gorevler"), ("lists", "/liste"), ("calendar", "/bugun"), ("calendar", "/hafta")])
def test_disabled_plugin_commands_refuse_friendly(plugin, cmd):
    a, _ = asst(**{plugin: False})
    r = say(a, cmd)
    assert "özelliği ayarlarda kapalı" in r and f"{plugin} = true" in r


def test_disabled_wordings_refuse_and_write_nothing():
    a, st = asst(tasks=False)
    assert "ayarlarda kapalı" in say(a, "görev ekle çamaşır")
    assert st.open_tasks(a.owner) == []
    a, st = asst(lists=False)
    assert "ayarlarda kapalı" in say(a, "alınacaklara süt ekle")
    assert st.open_pending(a.owner) is None
    a, st = asst(calendar=False)
    assert "ayarlarda kapalı" in say(a, "yarın 15:00 toplantı ekle")
    assert st.open_pending(a.owner) is None


def test_other_plugins_keep_working_when_one_is_off():
    a, st = asst(lists=False)
    assert "ayarlarda kapalı" not in say(a, "/gorevler")
    assert "ayarlarda kapalı" not in say(a, "yarın 15:00 toplantı ekle")
    assert st.open_pending(a.owner) is not None        # still previewed, never written directly


def test_preview_opened_before_a_switch_off_is_refused_at_confirm():
    a, st = asst()
    say(a, "yarın 15:00 diş hekimi ekle")
    assert st.open_pending(a.owner) is not None
    a.plugins = Plugins(frozenset({"calendar"}))
    assert "ayarlarda kapalı" in say(a, "evet")
    assert st.open_pending(a.owner) is None and a.hub.events(a.clock(), a.clock().replace(year=2027)) == []


def test_agent_tools_of_a_disabled_plugin_are_refused():
    a, st = asst(tasks=False)
    t = AgentTools(a)
    text, err = t.call("task_add", {"title": "x"})
    assert err and text.startswith("PLUGIN_DISABLED") and st.open_tasks(a.owner) == []
    assert t.call("note_get", {})[1] is False          # core tools stay on


def test_import_and_list_cli_proposals_respect_the_switch():
    a, st = asst(calendar=False, lists=False)
    assert "ayarlarda kapalı" in "\n".join(a.propose_import([], datetime(2026, 11, 19, tzinfo=IST)))
    assert "ayarlarda kapalı" in "\n".join(a.propose_list_add("alinacaklar", ["x"], datetime(2026, 11, 19, tzinfo=IST)))


def test_digest_without_calendar_omits_events_but_keeps_tasks():
    now = datetime(2026, 11, 19, 8, 30, tzinfo=IST)
    m = morning_digest([ev()], now, tasks=["Fatura öde"], calendar=False)
    assert "Dis" not in m and "Fatura öde" in m
    e = evening_digest([ev(day=20)], now, tasks=["Fatura öde"], calendar=False)
    assert "Dis" not in e and "Fatura öde" in e
    assert "Dis" in morning_digest([ev()], now, tasks=["x"])      # default unchanged


def _sched(plugins, events, **kw):
    n = StubNotifier()
    s = Scheduler(time(8, 30), time(20, 0), lambda t: events, n, State(None), plugins=plugins, **kw)
    return s, n


def test_scheduler_drops_disabled_sections():
    now = datetime(2026, 11, 19, 8, 30, tzinfo=IST)
    s, n = _sched(Plugins(frozenset({"tasks"})), [ev()], tasks=lambda: ["Gizli görev"])
    s.tick(now)
    assert "Dis" in n.sent[0] and "Gizli görev" not in n.sent[0]
    s, n = _sched(Plugins(frozenset({"calendar"})), [ev()], tasks=lambda: ["Fatura öde"])
    s.tick(now)
    assert "Fatura öde" in n.sent[0] and "Dis" not in n.sent[0]


def test_scheduler_digests_off_sends_no_digest_and_reminders_off_no_reminder():
    now = datetime(2026, 11, 19, 8, 30, tzinfo=IST)
    s, n = _sched(Plugins(frozenset({"digests"})), [])
    s.tick(now)
    assert n.sent == []
    soon = Event("Dis", now.replace(minute=50), now.replace(hour=9, minute=50), "p", uid="d")
    s, n = _sched(Plugins(frozenset({"digests", "reminders"})), [soon])
    s.tick(now)
    assert n.sent == []
    s, n = _sched(Plugins(frozenset({"digests"})), [soon])
    s.tick(now)
    assert any("Dis" in x for x in n.sent)


def test_scheduler_calendar_off_never_fetches_events():
    now = datetime(2026, 11, 19, 8, 30, tzinfo=IST)
    calls = []
    n = StubNotifier()
    Scheduler(time(8, 30), time(20, 0), lambda t: calls.append(t) or [], n, State(None), plugins=Plugins(frozenset({"calendar"}))).tick(now)
    assert calls == [] and n.sent == []          # no tasks, nothing to say: no empty digest


def test_plan_digest_unchanged_by_default():
    now = datetime(2026, 11, 19, 22, 0, tzinfo=IST)
    assert plan_digest([ev(day=20)], now)
