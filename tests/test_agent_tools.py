"""ADR 0019: the typed tools. Write tools only create DRAFTS; nothing reaches the calendar without a code-minted grant."""
import json

import pytest

from agent_support import NOW, OWNER, ev, make
from sebastian import i18n
from sebastian.agent_tools import TOOL_NAMES, TOOL_SPECS, WRITE_TOOLS, AgentTools

EXPECTED = {"calendar_range", "find_events", "propose_add_event", "propose_edit_event", "propose_delete_event", "show_draft",
            "edit_draft", "commit_draft", "list_get", "list_add", "list_check", "list_remove", "task_add", "task_set_due", "task_list",
            "task_done", "note_set", "note_get", "recent_messages"}


def tools(events=()):
    a, hub, st, clock, _ = make(events)
    return AgentTools(a), a, hub, st


def test_exactly_the_decided_tools_and_no_chat_parameter():
    assert set(TOOL_NAMES) == EXPECTED and len(TOOL_NAMES) == 19
    for name, spec in TOOL_SPECS.items():
        assert not any("chat" in k.lower() for k in spec["inputSchema"]["properties"]), name
        assert spec["inputSchema"]["additionalProperties"] is False


def test_unknown_tool_and_bad_arguments_are_errors_not_crashes():
    t, *_ = tools()
    assert t.call("bash", {"cmd": "ls"})[1] is True
    assert t.call("propose_add_event", {"title": 5, "date": "x"})[1] is True
    assert t.call("calendar_range", None)[1] is True


def test_propose_add_makes_a_versioned_draft_and_writes_nothing():
    t, a, hub, st = tools()
    text, err = t.call("propose_add_event", {"title": "Diş hekimi", "date": "2026-11-20", "time": "15:00"})
    assert not err and "DRAFT v" in text
    row = st.open_pending(OWNER)
    assert row["intent"] == "add_event" and hub.events(NOW, NOW.replace(year=2027)) == []
    assert t.effects.drafted == row["id"]
    assert "Diş hekimi" in t.call("show_draft", {})[0] and "onaylar mısınız" in t.call("show_draft", {})[0]


def test_missing_time_is_refused_not_guessed_and_past_dates_too():
    t, a, hub, st = tools()
    assert t.call("propose_add_event", {"title": "Berber", "date": "2026-11-20"})[0].startswith("MISSING_TIME")
    assert t.call("propose_add_event", {"title": "Berber", "date": "2026-11-01", "time": "10:00"})[1]
    assert t.call("propose_add_event", {"title": "Berber", "date": "2026-11-20", "all_day": True})[1] is False
    assert st.open_pending(OWNER) is not None


def test_a_second_add_extends_the_draft_with_a_new_version():
    t, a, hub, st = tools()
    t.call("propose_add_event", {"title": "A", "date": "2026-11-20", "time": "09:00"})
    v1 = st.open_pending(OWNER)["id"]
    t.call("propose_add_event", {"title": "B", "date": "2026-11-21", "time": "10:00"})
    row = st.open_pending(OWNER)
    assert row["id"] != v1 and len(json.loads(row["args_json"])["drafts"]) == 2
    assert st.get_pending(v1)["status"] == "superseded"


def test_existing_event_is_not_proposed_again():
    t, a, hub, st = tools([ev("Diş hekimi", 20, 15)])
    text, err = t.call("propose_add_event", {"title": "Diş hekimi", "date": "2026-11-20", "time": "15:00"})
    assert err and text.startswith("ALREADY_EXISTS") and st.open_pending(OWNER) is None


def test_edit_draft_bumps_the_version_and_kills_the_grant():
    t, a, hub, st = tools()
    t.call("propose_add_event", {"title": "A", "date": "2026-11-20", "time": "09:00"})
    v1 = st.open_pending(OWNER)
    st.grant_mint(OWNER, v1["id"], v1["args_hash"], NOW)
    text, err = t.call("edit_draft", {"draft_version": v1["id"], "ops": [{"op": "set", "items": "all", "field": "time", "value": "11:00"}]})
    v2 = st.open_pending(OWNER)
    assert not err and v2["id"] != v1["id"] and "11:00" in v2["args_json"]
    assert t.call("commit_draft", {"draft_version": v2["id"]})[0].startswith("NOT_CONFIRMED")
    assert t.call("commit_draft", {"draft_version": v1["id"]})[0].startswith("VERSION_MISMATCH")
    assert hub.events(NOW, NOW.replace(year=2027)) == []


def test_edit_draft_validates_and_cancel_closes():
    t, a, hub, st = tools()
    t.call("propose_add_event", {"title": "A", "date": "2026-11-20", "time": "09:00"})
    v = st.open_pending(OWNER)["id"]
    assert t.call("edit_draft", {"draft_version": v, "ops": [{"op": "set", "items": [3], "field": "time", "value": "11:00"}]})[1]
    assert t.call("edit_draft", {"draft_version": v, "ops": [{"op": "set", "items": "all", "field": "time", "value": "25:99"}]})[1]
    assert t.call("edit_draft", {"draft_version": v, "ops": [{"op": "remove", "item": 1}]})[1]      # the last item cannot go
    assert t.call("edit_draft", {"draft_version": v, "ops": [{"op": "cancel"}]})[0] == "DRAFT_CANCELLED"
    assert st.open_pending(OWNER) is None


def test_commit_needs_a_grant_for_this_exact_version_and_is_single_use():
    t, a, hub, st = tools()
    t.call("propose_add_event", {"title": "A", "date": "2026-11-20", "time": "09:00"})
    row = st.open_pending(OWNER)
    assert t.call("commit_draft", {"draft_version": row["id"]})[0].startswith("NOT_CONFIRMED")
    st.grant_mint(OWNER, row["id"], "0" * 64, NOW)                     # wrong hash: no grant for this version
    assert t.call("commit_draft", {"draft_version": row["id"]})[1]
    st.grant_mint(OWNER, row["id"], row["args_hash"], NOW)
    text, err = t.call("commit_draft", {"draft_version": row["id"]})
    assert not err and text.startswith("COMMITTED") and len(hub.events(NOW, NOW.replace(year=2027))) == 1
    assert t.effects.committed and "Ekledim" in t.effects.committed[0]
    assert t.call("commit_draft", {"draft_version": row["id"]})[1]     # no draft left
    assert len(hub.events(NOW, NOW.replace(year=2027))) == 1


def test_delete_and_edit_drafts_go_through_the_same_gate():
    t, a, hub, st = tools([ev("Toplantı", 20, 14, eid="e1")])
    assert t.call("propose_delete_event", {"event_id": "e1"})[1] is False
    row = st.open_pending(OWNER)
    assert row["intent"] == "delete_event"
    assert t.call("commit_draft", {"draft_version": row["id"]})[1] and len(hub.events(NOW, NOW.replace(year=2027))) == 1
    assert t.call("propose_add_event", {"title": "X", "date": "2026-11-21", "time": "10:00"})[0].startswith("DRAFT_OPEN")
    assert t.call("edit_draft", {"draft_version": row["id"], "ops": [{"op": "cancel"}]})[0] == "DRAFT_CANCELLED"
    assert t.call("propose_edit_event", {"event_id": "e1", "changes": {"time": "16:00"}})[1] is False
    assert st.open_pending(OWNER)["intent"] == "edit_event"
    assert t.call("propose_edit_event", {"event_id": "nope", "changes": {"time": "16:00"}})[1]


def test_calendar_reads_return_ids_and_unknown_ids_are_refused():
    t, a, hub, st = tools([ev("Toplantı", 20, 14, eid="e1"), ev("Berber", 25, 11, eid="e2")])
    out = t.call("calendar_range", {"start_date": "2026-11-19", "end_date": "2026-11-21"})[0]
    assert "[e1]" in out and "[e2]" not in out and "14:00-15:00" in out
    assert "[e2]" in t.call("find_events", {"query": "berber"})[0]
    assert t.call("find_events", {"query": "yokyok"})[0] == "NO_MATCH"
    assert t.call("calendar_range", {"start_date": "2026-11-19", "end_date": "2027-06-01"})[1]


def test_list_tools_immediate_adds_and_drafted_removals():
    t, a, hub, st = tools()
    out = t.call("list_add", {"items": ["süt", "kahve"]})[0]
    assert "ADDED: Süt, Kahve" in out and t.effects.immediate
    assert "ALREADY_THERE: Süt" in t.call("list_add", {"items": ["süt"]})[0]
    assert "CHECKED: Süt" in t.call("list_check", {"items": ["süt"]})[0]
    assert t.call("list_remove", {"items": ["kahve"]})[1] is False
    row = st.open_pending(OWNER)
    assert row["intent"] == "list_remove" and [r["text"] for r in st.list_items(OWNER, "alinacaklar")] == ["Kahve"]
    assert t.call("commit_draft", {"draft_version": row["id"]})[1]
    assert t.call("list_get", {"list": "yok"})[1]


def test_task_and_note_tools_persist_in_the_db():
    t, a, hub, st = tools([ev("Çamaşır", 19, 18, eid="c1")])
    assert t.call("task_add", {"title": "çamaşır"})[0].startswith("TASK_ADDED")
    assert t.call("task_add", {"title": "çamaşır"})[0].startswith("ALREADY_THERE")
    assert "1. Çamaşır" in t.call("task_list", {})[0]
    out = t.call("task_done", {"title": "çamaşır"})[0]
    assert out.startswith("TASK_DONE") and "[c1]" in out and st.open_tasks(OWNER) == []
    assert t.call("note_set", {"key": "cop_sure", "value": "çöp = 15 dk iş"})[0] == "NOTE_SAVED: cop_sure"
    assert t.call("note_get", {"key": "cop_sure"})[0] == "çöp = 15 dk iş"
    assert "cop_sure" in t.call("note_get", {})[0]
    assert t.call("note_set", {"key": "bad key!", "value": "x"})[1]
    assert t.call("note_set", {"key": "cop_sure", "value": ""})[0].startswith("NOTE_DELETED") and t.call("note_get", {})[0] == "NO_NOTES"


def test_recent_messages_is_the_owner_chat_only():
    t, a, hub, st = tools()
    st.log(OWNER, "merhaba", "in", NOW)
    st.log(999, "baska sohbet", "in", NOW)
    out = t.call("recent_messages", {"n": 5})[0]
    assert "merhaba" in out and "baska" not in out


def test_write_tools_never_touch_the_hub_directly():
    t, a, hub, st = tools([ev("Toplantı", 20, 14, eid="e1")])
    before = list(hub._events)
    t.call("propose_add_event", {"title": "A", "date": "2026-11-21", "time": "09:00"})
    t.call("edit_draft", {"draft_version": st.open_pending(OWNER)["id"], "ops": [{"op": "cancel"}]})
    t.call("propose_delete_event", {"event_id": "e1"})
    assert hub._events == before and set(WRITE_TOOLS) <= set(TOOL_NAMES)
