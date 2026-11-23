"""ADR 0023: model assist. The provider is a fake (the CLI edge is injected); nothing here calls a model."""
import json
import subprocess

import pytest

from sebastian import assist as am, nl as nlmod
from test_assistant import make, say, NOW, OWNER


class Fake:
    model, temperature_note = "fake-model", "test"

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.prompts = answer, error, []

    def call(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise am.AssistError(self.error)
        return self.answer, {"ms": 5, "cost_usd": 0.001, "model": "fake-model", "raw": json.dumps(self.answer)}


class Down:
    name, model = "down", "down"

    def parse(self, message, context):
        raise nlmod.ProviderError("down", reason="network")


M = "bahce icin her sey tertip"           # no verb the rules know: they cannot read it


def setup(mode, fake, titles=("Bahçeyi sula", "Çamaşır yıka")):
    a, hub, st, _ = make()
    for t in titles:
        st.add_task(OWNER, t, NOW, None)
    a.nl = nlmod.NLService("claude", {"claude": Down(), "qwen": Down()}, consent=True)
    a.assist = am.AssistService(mode, fake)
    return a, st


def tid(st, i):
    return f"T{st.open_tasks(OWNER)[i]['id']}"


def done_for(st, i, span=M):
    return {"clauses": [{"span": span, "kind": "done", "ids": [tid(st, i)], "scope": "none"}]}


def open_titles(st):
    return [r["title"] for r in st.open_tasks(OWNER)]


def audits(st):
    return [json.loads(r["detail"]) for r in st.audit_rows("nl_assist")]


def test_shadow_logs_and_compares_but_never_acts():
    f = Fake()
    a, st = setup("shadow", f)
    f.answer = done_for(st, 0)
    out = say(a, M)
    assert open_titles(st) == ["Bahçeyi sula", "Çamaşır yıka"]                       # nothing closed by the model
    d = audits(st)[0]
    assert d["mode"] == "shadow" and d["model_clauses"] == [["done", [tid(st, 0)]]] and "raw" in d and d["cost_usd"] == 0.001
    assert out


def test_promoted_model_acts_only_through_a_preview_with_the_title():
    f = Fake()
    a, st = setup("on", f)
    f.answer = done_for(st, 0)
    out = say(a, M)
    assert "Bahçeyi sula" in out and "(evet / hayır)" in out and len(open_titles(st)) == 2
    say(a, "evet")
    assert open_titles(st) == ["Çamaşır yıka"]


def test_an_id_outside_the_supplied_list_rejects_the_whole_answer():
    f = Fake()
    a, st = setup("on", f)
    f.answer = {"clauses": [{"span": M, "kind": "done", "ids": ["T9999"], "scope": "none"}]}
    say(a, M)
    assert len(open_titles(st)) == 2 and audits(st)[0]["rejected"] == "id_outside_list"


@pytest.mark.parametrize("answer,reason", [
    ({"clauses": [{"span": "uydurma", "kind": "done", "ids": ["T1"], "scope": "none"}]}, "span"),
    ({"clauses": [{"span": M, "kind": "explode", "ids": [], "scope": "none"}]}, "schema"),
    ({"clauses": [{"span": M, "kind": "done", "ids": [], "scope": "none"}]}, "no_id"),
    ({"clauses": [{"span": M, "kind": "defer", "ids": ["T1"], "scope": "none"}]}, "no_day"),
    ({"nonsense": 1}, "schema"), (None, "schema"),
])
def test_validation_rejects(answer, reason):
    f = Fake()
    a, st = setup("on", f)
    if isinstance(answer, dict) and answer.get("clauses"):
        for c in answer["clauses"]:
            c["ids"] = [tid(st, 0)] if c["ids"] else []
    f.answer = answer
    say(a, M)
    assert audits(st)[0]["rejected"] == reason and len(open_titles(st)) == 2


def test_a_failing_model_falls_back_to_the_rules():
    a, st = setup("on", Fake(error="timeout"))
    say(a, M)
    assert audits(st)[0]["rejected"] == "timeout" and len(open_titles(st)) == 2


def test_the_model_is_not_called_for_trivial_or_rules_confident_messages():
    f = Fake()
    a, st = setup("on", f)
    for text in ("evet", "tamam", "bugun ne var", "camasir bitti", "yarina kalsin mi?", "/gorevler", "gorev ekle berber"):
        say(a, text)
    assert f.prompts == []


def test_off_without_consent_or_when_mode_off():
    f = Fake()
    a, st = setup("off", f)
    say(a, M)
    a.assist, a.nl.consent = am.AssistService("on", f, enabled=False), False
    say(a, M)
    assert f.prompts == []


def test_defer_day_is_read_from_the_words_not_from_the_model():
    from datetime import date
    cands = am.Candidates({"T1": {"id": 1, "title": "Çamaşır yıka"}}, {})
    out = am.validate({"clauses": [{"span": "camasir konusu pazartesi", "kind": "defer", "ids": ["T1"], "scope": "none"}]},
                      "camasir konusu pazartesi", cands, date(2026, 11, 19))
    assert out[0]["day"] == date(2026, 11, 23) and out[0]["ids"] == ["T1"] and out[0]["model"]
    with pytest.raises(am.AssistError):                                   # a field the schema does not have is rejected
        am.validate({"clauses": [{"span": "camasir konusu pazartesi", "kind": "defer", "ids": ["T1"], "scope": "none", "day": "2030-01-01"}]},
                    "camasir konusu pazartesi", cands, date(2026, 11, 19))


def test_provider_command_pins_the_model_and_uses_a_schema(monkeypatch):
    calls = []

    def runner(cmd, inp, cwd, env, timeout):
        calls.append((cmd, timeout))
        return 0, json.dumps({"structured_output": {"clauses": []}, "total_cost_usd": 0.001, "modelUsage": {"claude-haiku-5-5": {}}})
    p = am.ClaudeAssistProvider(runner=runner)
    p.call("{}")
    cmd, timeout = calls[0]
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-5-5" and "--json-schema" in cmd and "--tools" in cmd and timeout == am.TIMEOUT


def test_provider_timeout_is_an_assist_error():
    def runner(*a):
        raise subprocess.TimeoutExpired("claude", 1)
    with pytest.raises(am.AssistError) as e:
        am.ClaudeAssistProvider(runner=runner).call("{}")
    assert e.value.reason == "timeout"


def test_recorded_model_answers_all_pass_validation():
    """Cassette of the real spike calls (claude-haiku-5-5, ADR 0023): every recorded answer validates against the candidate list that was in its
    own prompt, so the contract (strict schema, ids from the list, spans from the message) holds for real output, offline."""
    from datetime import date
    from pathlib import Path
    cas = json.loads((Path(__file__).parent / "cassettes" / "assist_spike.json").read_text(encoding="utf-8"))
    assert len(cas) >= 30
    for prompt, (raw, meta) in cas.items():
        p = json.loads(prompt)
        cands = am.Candidates({t["id"]: {"id": int(t["id"][1:]), "title": t["title"]} for t in p["tasks"]}, {})
        cands.events = {e["id"]: type("E", (), {"title": e["title"]})() for e in p["events"]}
        try:
            am.validate(raw, p["message"], cands, date(2026, 11, 19))
        except am.AssistError as exc:
            assert exc.reason in ("no_day", "no_id"), (p["message"], raw, exc.reason)      # the only rejections allowed: a model that was unsure


def test_an_unsure_model_answer_leaves_the_rules_reading_standing():
    f = Fake()
    a, st = setup("on", f, titles=("Kargoyu postaneden al", "Ayakkabıyı tamire ver"))
    m = "kargoyu aldim tamirci henuz haber vermedi"
    f.answer = {"clauses": [{"span": "kargoyu aldim", "kind": "done", "ids": [tid(st, 0)], "scope": "none"},
                            {"span": "tamirci henuz haber vermedi", "kind": "other", "ids": [], "scope": "none"}]}
    out = say(a, m)
    assert "Kargoyu postaneden al" in out and open_titles(st) == ["Ayakkabıyı tamire ver"]


def test_default_mode_is_off_after_the_kill_criterion(monkeypatch):
    class N:
        enabled, mode = True, "claude"
    assert am.AssistService.from_env({}, N()).mode == "off" and not am.AssistService.from_env({}, N()).active
    assert am.AssistService.from_env({"SEBASTIAN_NL_ASSIST": "shadow"}, N()).active
