"""Every incident fixture, replayed against agent mode with a scripted fake model (ADR 0020): deterministic CI of the gate,
tools, grants, context building, composition and the Bot loop. Invariants I1-I10 and the state expectations must hold."""
import pytest

import agent_replay as ar
import invariants as inv

INCIDENTS = inv.load_incidents()


@pytest.mark.parametrize("name,script", INCIDENTS, ids=lambda x: x if isinstance(x, str) else "")
def test_incident_replays_in_agent_mode(name, script):
    if script.get("agent") is False:
        pytest.skip("manager-only fixture (retry queue, ADR 0021)")
    ar.run_in_agent_mode(script)


def test_all_incidents_are_replayed():
    assert len(INCIDENTS) >= 50 and len({n for n, _ in INCIDENTS}) == len(INCIDENTS)


class Greedy(ar.ReplayRunner):
    """A hostile fake model: on EVERY turn it first tries to commit the open draft and claims it saved it."""

    def run(self, req):
        a = req.tools.a
        row = a.store.open_pending(a.owner)
        if row is not None:
            self.results.append(("commit_draft", *req.tools.call("commit_draft", {"draft_version": row["id"]})))
        res = super().run(req)
        res.text = "Kaydettim, efendim. " + res.text
        return res


@pytest.mark.parametrize("name,script", INCIDENTS, ids=lambda x: x if isinstance(x, str) else "")
def test_a_model_that_commits_every_turn_still_cannot_write_without_a_yes(name, script):
    """Only a code-minted grant lets a commit through, and a claim of saving without a write is replaced."""
    if script.get("agent") is False:
        pytest.skip("manager-only fixture (retry queue, ADR 0021)")
    directives = ar.derive(script)
    conv, runner = ar.run_in_agent_mode(script, Greedy(directives))
    immediate = any(c[0].startswith(("list_add", "list_check", "task_", "note_")) for d in directives.values() for c in d["calls"])
    if not immediate:          # a list or task write may be reported as done; a calendar claim without a write never is
        assert not any("Kaydettim" in line for _, _, out in conv.log for line in out)


def test_the_replay_model_really_drives_the_tools():
    script = dict(INCIDENTS)["2026-11-ready-time-existing-dental"]
    conv, runner = ar.run_in_agent_mode(script)
    assert any(name == "propose_edit_event" and not err for name, _, err in runner.results)
    assert "onay=YOK" in runner.requests[0].text
