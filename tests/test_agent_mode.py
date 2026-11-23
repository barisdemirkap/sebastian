"""ADR 0019: agent mode end to end with a scripted fake model: the gate and grants, replies composed by code, context
building, sessions, the fallback chain, circuit breaker, daily cap and instant rollback (also mid-draft)."""
import json
from datetime import timedelta

import pytest

from agent_support import NOW, OWNER, ScriptedRunner, ev, make, say
from sebastian import agent as agentmod, i18n
from sebastian.agent import RunResult, AgentMode, PROMPT_VERSION, EXPECTED_INIT_TOOLS

ADD = ("propose_add_event", {"title": "Diş hekimi", "date": "2026-11-20", "time": "15:00"})


def events(hub):
    return [e for e in hub._events]


def open_draft(a, runner, text="yarın 15:00 diş hekimi"):
    runner.push("Hemen, efendim.", [ADD])
    return say(a, text)


# ---------------------------------------------------------------- the happy path and the gate
def test_draft_then_plain_yes_commits_once_with_a_code_receipt():
    a, hub, st, clock, runner = make()
    out = open_draft(a, runner)
    assert out[0] == "Hemen, efendim." and "onaylar mısınız" in out[-1] and events(hub) == []      # preview is LAST and by code
    assert a.keyboard()["inline_keyboard"][0][0]["text"] == "Kaydet"
    runner.push("Kaydettim efendim.", [("commit_draft", {"draft_version": "@draft"})])
    out = say(a, "evet")
    assert out == ["Emredersiniz, efendim. Ekledim: Diş hekimi, Cuma 20 Kasım 2026 15:00-16:00"]     # the model's words are dropped
    assert len(events(hub)) == 1 and st.open_pending(OWNER) is None and st.grant_live(OWNER) is None
    runner.push("Bir şey yok.")
    say(a, "evet")
    assert len(events(hub)) == 1


def test_the_model_cannot_commit_without_the_users_yes():
    a, hub, st, clock, runner = make()
    runner.push("Ekledim efendim.", [ADD, ("commit_draft", {"draft_version": "@draft"})])
    out = say(a, "yarın 15:00 diş hekimi")
    assert events(hub) == [] and runner.results[-1][0].startswith("NOT_CONFIRMED")
    assert "Ekledim efendim" not in "\n".join(out) and "onaylar mısınız" in out[-1]               # a false claim is replaced


def test_evet_ama_is_an_edit_never_a_grant():
    a, hub, st, clock, runner = make()
    open_draft(a, runner)
    runner.push("Saati değiştirdim.", [("edit_draft", {"draft_version": "@draft", "ops": [{"op": "set", "items": "all", "field": "time", "value": "16:00"}]}),
                                       ("commit_draft", {"draft_version": "@draft"})])
    out = say(a, "evet ama saati 16:00 yap")
    assert events(hub) == [] and "16:00" in out[-1] and runner.results[-1][0].startswith("NOT_CONFIRMED")
    assert "onay=YOK" in runner.requests[-1].text.splitlines()[0]                                  # the system line said so


def test_the_system_line_carries_the_grant_state_and_version():
    a, hub, st, clock, runner = make()
    open_draft(a, runner)
    first = runner.requests[0].text
    assert "onay=YOK" in first.splitlines()[0] and "taslak=yok" in first.splitlines()[0]
    pid = st.open_pending(OWNER)["id"]
    runner.push("Tamam.", [("commit_draft", {"draft_version": "@draft"})])
    say(a, "tamam")
    line = runner.requests[-1].text.splitlines()[0]
    assert "onay=EVET" in line and f"taslak=v{pid}" in line


def test_a_forgotten_commit_is_done_by_code_on_a_valid_yes():
    a, hub, st, clock, runner = make()
    open_draft(a, runner)
    runner.push("Peki.")                                   # the model says something and does not call commit_draft
    out = say(a, "evet")
    assert len(events(hub)) == 1 and out[0].startswith("Emredersiniz, efendim. Ekledim:")


def test_another_bot_message_between_preview_and_yes_kills_the_grant():
    a, hub, st, clock, runner = make()
    open_draft(a, runner)
    st.log(OWNER, "Günaydın, efendim. Bugün takvimde bir şey yok.", "out", clock())               # a digest arrives
    runner.push("Önizlemeyi yeniden gösteriyorum.", [("commit_draft", {"draft_version": "@draft"})])
    out = say(a, "evet")
    assert events(hub) == [] and "onaylar mısınız" in out[-1]                                       # preview shown again
    runner.push("", [("commit_draft", {"draft_version": "@draft"})])
    say(a, "evet")                                                                                   # now the preview was last
    assert len(events(hub)) == 1


def test_delete_keeps_the_stricter_rule():
    a, hub, st, clock, runner = make([ev("Toplantı", 20, 14, eid="e1")])
    runner.push("", [("propose_delete_event", {"event_id": "e1"})])
    out = say(a, "toplantıyı sil")
    assert "SİLMEMİ" in out[-1]
    runner.push("x")
    out = say(a, "evet")                                                                              # plain yes: hint + preview again
    assert out[0].startswith("Silmem için") and "SİLMEMİ" in out[-1] and len(runner.requests) == 1 and len(events(hub)) == 1
    runner.push("", [("commit_draft", {"draft_version": "@draft"})])
    out = say(a, "evet sil")
    assert out[0].startswith("Emredersiniz, efendim. Sildim:") and events(hub) == []


def test_pure_no_cancels_in_code_and_revokes_everything():
    a, hub, st, clock, runner = make()
    open_draft(a, runner)
    out = say(a, "hayır")
    assert out == [i18n.t("cancelled")] and st.open_pending(OWNER) is None and len(runner.requests) == 1


def test_the_gate_conditions_directly():
    a, hub, st, clock, runner = make()
    open_draft(a, runner)
    row = st.open_pending(OWNER)
    mode = a.agent
    assert not mode.mint_grant(row, "evet 16:00 olsun", NOW) and not mode.mint_grant(row, "evet ama", NOW)
    assert mode.mint_grant(row, "Evet!", NOW) and st.grant_live(OWNER)["pending_id"] == row["id"]
    st.grant_revoke_all(OWNER)
    assert not mode.mint_grant(row, "evet", NOW + timedelta(minutes=11))          # expired draft


# ---------------------------------------------------------------- context and sessions
def test_context_block_is_built_by_code_with_events_lists_tasks_notes_and_draft():
    a, hub, st, clock, runner = make([ev("Toplantı", 20, 14, eid="e1"), ev("Uzak", 30, 9, eid="far")])
    st.add_list_item(OWNER, "alinacaklar", "Süt", NOW)
    st.add_task(OWNER, "Çamaşır", NOW)
    st.note_set(OWNER, "cop_sure", "15 dk iş", NOW)
    open_draft(a, runner)
    runner.push("Peki.")
    say(a, "ne var")
    text = runner.requests[-1].text
    assert text.startswith("[SİSTEM bugün=2026-11-19 saat=10:00 hitap=") and "taslak=v" in text.splitlines()[0]
    for needle in ("NOW: 2026-11-19 Perşembe 10:00", "[e1] 2026-11-20 Fri 14:00-15:00 Toplantı", "1. Süt", "1. Çamaşır", "cop_sure=15 dk iş",
                   "DRAFT v", "Diş hekimi", "\n---\nne var"):
        assert needle in text, needle
    assert "far" not in text                                    # only the next 7 days


def test_session_is_created_resumed_and_rotated_by_turns_idle_and_prompt_version():
    a, hub, st, clock, runner = make(max_turns=2)
    for _ in range(2):
        runner.push("Peki.")
        say(a, "merhaba")
    first, second = runner.requests
    assert first.first and not second.first and first.session_id == second.session_id
    runner.push("Peki.")
    say(a, "merhaba")                                           # turns >= 2: rotate
    third = runner.requests[2]
    assert third.first and third.session_id != first.session_id and "yeni_oturum=1" in third.text and "RECENT MESSAGES" in third.text
    clock.now += timedelta(hours=7)                             # idle
    runner.push("Peki.")
    say(a, "merhaba")
    assert runner.requests[3].first and runner.requests[3].session_id != third.session_id
    st.c.execute("UPDATE agent_sessions SET prompt_version='old'")
    runner.push("Peki.")
    say(a, "merhaba")
    assert runner.requests[4].first and st.agent_session(OWNER)["prompt_version"] == PROMPT_VERSION


def test_facts_survive_rotation_because_they_live_in_the_db():
    a, hub, st, clock, runner = make(max_turns=1)
    runner.push("Not aldım.", [("note_set", {"key": "default_reminder_min", "value": "20"})])
    say(a, "hatırlatma 20 dk önce olsun")
    runner.push("Peki.")
    say(a, "merhaba")
    assert runner.requests[1].first and "default_reminder_min=20" in runner.requests[1].text


# ---------------------------------------------------------------- fallback, breaker, cap, init check
def failing(err="claude error"):
    return lambda req: RunResult(error=err)


def test_agent_failure_falls_back_to_the_dialogue_manager_and_keeps_working():
    a, hub, st, clock, runner = make(scripts=[failing()])
    out = say(a, "yarın saat 15:00 diş hekimi ekle")
    assert "onaylar mısınız" in out[0] and st.open_pending(OWNER) is not None       # the manager made the preview
    assert say(a, "evet")[0].startswith("Emredersiniz, efendim. Ekledim:") and len(events(hub)) == 1


def test_breaker_opens_after_three_failures_and_closes_after_the_cooldown():
    a, hub, st, clock, runner = make(scripts=[failing(), failing(), failing()])
    for _ in range(3):
        say(a, "bugün ne var")
    assert len(runner.requests) == 3 and not a.agent.active(clock())
    say(a, "bugün ne var")
    assert len(runner.requests) == 3                                                   # skipped: breaker open
    clock.now += timedelta(minutes=11)
    runner.push("Peki.")
    say(a, "merhaba")
    assert len(runner.requests) == 4 and int(st.get_meta("agent_fail_n")) == 0


def test_daily_cap_sends_everything_to_the_manager():
    a, hub, st, clock, runner = make(daily_cap=2)
    for _ in range(4):
        runner.push("Peki.")
        say(a, "merhaba")
    assert len(runner.requests) == 2
    clock.now += timedelta(days=1)
    runner.push("Peki.")
    say(a, "merhaba")
    assert len(runner.requests) == 3


def test_init_tools_must_be_exactly_the_expected_ones():
    bad = lambda req: RunResult(text="Ekledim", init_tools=sorted(EXPECTED_INIT_TOOLS) + ["Bash"])
    good = lambda req: RunResult(text="Peki, efendim.", init_tools=sorted(EXPECTED_INIT_TOOLS))
    a, hub, st, clock, runner = make(scripts=[bad, good])
    out = say(a, "merhaba")
    assert "Ekledim" not in "\n".join(out) and st.audit_rows("agent_failed")                 # fell back, nothing of it used
    assert say(a, "merhaba") == ["Peki, efendim."]


def test_a_crashing_runner_never_breaks_the_chat():
    class Boom:
        def run(self, req):
            raise RuntimeError("x")
    a, hub, st, clock, _ = make()
    a.agent.runner = Boom()
    assert "Emrinizdeyim" in say(a, "merhaba")[0]


def test_last_rung_is_read_only_when_even_the_manager_fails():
    a, hub, st, clock, runner = make(scripts=[failing()])
    a._handle = lambda *args, **kw: 1 / 0
    assert say(a, "yarın saat 15:00 toplantı ekle") == [i18n.t("readonly_mode")]


def test_readonly_mode_answers_reads_and_refuses_writes():
    a, hub, st, clock, runner = make([ev("Toplantı", 19, 14)])
    a.agent, a.readonly = None, True
    assert "Toplantı" in say(a, "bugün ne var")[0]
    assert say(a, "yarın saat 15:00 toplantı ekle") == [i18n.t("readonly_mode")] and st.open_pending(OWNER) is None
    assert a.callback(OWNER, "k", 1, "00000000")[0] == i18n.t("readonly_mode")


# ---------------------------------------------------------------- rollback and flips
def test_ajan_switch_is_instant_and_mid_draft_flip_loses_and_double_writes_nothing():
    a, hub, st, clock, runner = make()
    open_draft(a, runner)                                                                 # draft made in agent mode
    pid = st.open_pending(OWNER)["id"]
    assert say(a, "/ajan kapat") == [i18n.t("agent_turned_off")]
    assert st.open_pending(OWNER)["id"] == pid                                            # nothing lost
    out = say(a, "evet")                                                                  # the manager confirms it
    assert out[0].startswith("Emredersiniz, efendim. Ekledim:") and len(events(hub)) == 1 and len(runner.requests) == 1
    assert say(a, "/ajan ac") == [i18n.t("agent_turned_on")]
    runner.push("Peki.")
    say(a, "evet")                                                                        # agent mode: nothing to confirm
    assert len(events(hub)) == 1
    assert say(a, "/ajan")[0] == i18n.t("agent_status_on")


def test_flip_from_manager_to_agent_mid_draft_the_agent_sees_the_draft_and_the_yes_works():
    a, hub, st, clock, runner = make()
    a.agent.set_switch(False)
    out = say(a, "yarın saat 15:00 diş hekimi ekle")                                      # manager draft
    assert "onaylar mısınız" in out[-1]
    a.agent.set_switch(True)
    runner.push("Peki.", [("commit_draft", {"draft_version": "@draft"})])
    out = say(a, "evet")
    assert "taslak=v" in runner.requests[-1].text.splitlines()[0] and "onay=EVET" in runner.requests[-1].text.splitlines()[0]
    assert len(events(hub)) == 1 and out[0].startswith("Emredersiniz, efendim. Ekledim:")


def test_button_save_still_works_in_agent_mode_and_runs_once():
    a, hub, st, clock, runner = make()
    open_draft(a, runner)
    pid = st.open_pending(OWNER)
    h8 = pid["args_hash"][:8]
    alert, replies = a.callback(OWNER, "k", pid["id"], h8)
    assert alert is None and replies[0].startswith("Emredersiniz, efendim. Ekledim:") and len(events(hub)) == 1
    assert a.callback(OWNER, "k", pid["id"], h8)[0] == i18n.t("btn_stale") and len(events(hub)) == 1


def test_commands_and_other_chats_never_reach_the_model():
    a, hub, st, clock, runner = make()
    assert "Komutlar" in say(a, "/komutlar")[0]
    assert a.handle(999, "merhaba") == [] and runner.requests == []


def test_replies_hold_at_most_one_form_of_address_even_from_the_model():
    a, hub, st, clock, runner = make()
    runner.push("Efendim, hemen yapıyorum efendim, Ahmet Bey.")
    out = say(a, "merhaba")[0]
    assert sum(out.lower().count(f) for f in ("efendim", "barış bey")) == 1


def test_from_env_needs_agent_value_and_consent():
    a, *_ = make()
    assert AgentMode.from_env(a, {"SEBASTIAN_NL": "claude", "SEBASTIAN_NL_CONSENT": "1"}) is None
    assert AgentMode.from_env(a, {"SEBASTIAN_NL": "agent"}) is None
    m = AgentMode.from_env(a, {"SEBASTIAN_NL": "agent", "SEBASTIAN_NL_CONSENT": "1", "SEBASTIAN_AGENT_DAILY_CAP": "5", "SEBASTIAN_AGENT_MODEL": "haiku"})
    assert m is not None and m.daily_cap == 5 and m.runner.model == "haiku" and m.disabled_reason is None


def test_startup_self_test_passes_and_a_failure_disables_agent_mode(monkeypatch):
    assert agentmod.self_test() == []
    a, *_ = make()
    monkeypatch.setattr(agentmod, "self_test", lambda: ["gate"])
    m = AgentMode.from_env(a, {"SEBASTIAN_NL": "agent", "SEBASTIAN_NL_CONSENT": "1"})
    a.agent = m
    assert not m.active(NOW) and "Emrinizdeyim" in say(a, "merhaba")[0]                  # disabled: the manager answers
