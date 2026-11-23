import json
import subprocess
from datetime import date
import pytest
from sebastian import nl
from sebastian.nl import ClaudeCodeProvider, NLService, ProviderError, QwenProvider, SchemaError, validate, extract_json

TODAY = date(2026, 11, 18)


def test_extract_json_tolerates_fences_and_think():
    assert extract_json('<think>hmm</think>```json\n{"intent": "help"}\n``` bye') == {"intent": "help"}
    for bad in ("", "no json", "[1]", None):
        with pytest.raises(ProviderError):
            extract_json(bad)


def test_validate_add_event_coerces_and_drops_invented_values():
    raw = {"intent": "add_event", "title": "Diş hekimi", "date": "2026-11-19", "time": "15:00", "duration_min": "45",
           "reminder_min": 60, "recurrence": "weekly", "evil": "ignored"}
    out = validate(raw, "yarın saat 15:00 diş hekimi", TODAY)
    assert out["date"] == "2026-11-19" and out["time"] == "15:00" and out["duration_min"] == 45 and out["recurrence"] == "weekly"
    assert "evil" not in out
    out = validate(raw, "diş hekimi ekle", TODAY)      # the owner said no date and no time: nothing may be invented
    assert out["date"] is None and out["time"] is None


def test_validate_birthday_forces_all_day_yearly():
    out = validate({"intent": "add_event", "title": "Ayşe doğum günü", "date": "2027-03-03", "tag": "birthday"}, "3 Mart", TODAY)
    assert out["all_day"] is True and out["recurrence"] == "yearly" and out["tag"] == "birthday"


@pytest.mark.parametrize("raw", [
    {"intent": "drop_database"}, {"intent": "add_event", "recurrence": "hourly"}, {"intent": "add_event", "time": "25:00"},
    {"intent": "add_event", "date": "2099-01-01"}, {"intent": "add_event", "duration_min": 99999},
    {"intent": "edit_event", "match": "x"}, {"intent": "edit_event", "match": "x", "changes": {}},
    {"intent": "delete_event"}, {"intent": "list_events", "range": "forever"}, "not a dict", None,
    {"intent": "add_event", "tag": "work"},
])
def test_validate_rejects(raw):
    with pytest.raises(SchemaError):
        validate(raw, "yarın 15:00 3 mart", TODAY)


def test_validate_move_becomes_edit_and_list():
    out = validate({"intent": "move_event", "match": "toplantı", "new_date": "2026-11-20"}, "cumaya ertele 20", TODAY)
    assert out["intent"] == "edit_event" and out["changes"] == {"date": "2026-11-20"}
    assert validate({"intent": "list", "range": "week"}, "bu hafta", TODAY)["range"] == "week"
    assert validate({"intent": "unclear", "question": "Hangi gün?"}, "x", TODAY)["question"] == "Hangi gün?"


def test_claude_provider_command_is_locked_down_and_env_is_scrubbed(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "s2")
    seen = {}

    def runner(cmd, text, cwd, env, timeout):
        seen.update(cmd=cmd, text=text, cwd=cwd, env=env, timeout=timeout)
        return 0, json.dumps({"result": '{"intent":"help"}', "modelUsage": {"claude-sonnet-x": {}}})
    p = ClaudeCodeProvider(runner=runner)
    assert p.parse("selam", {"today": "2026-11-18"}) == {"intent": "help"}
    cmd = seen["cmd"]
    assert cmd[:3] == ["claude", "-p", "--model"] and "--tools" in cmd and cmd[cmd.index("--tools") + 1] == ""
    assert "--no-session-persistence" in cmd and "--strict-mcp-config" in cmd
    assert not any(k for k in seen["env"] if "TELEGRAM" in k or "SECRET" in k)
    assert json.loads(seen["text"])["message"] == "selam" and p.model == "claude-sonnet-x"


@pytest.mark.parametrize("runner", [
    lambda *a: (1, "{}"), lambda *a: (0, "not json"), lambda *a: (0, json.dumps({"is_error": True})),
    lambda *a: (_ for _ in ()).throw(subprocess.TimeoutExpired("claude", 1)), lambda *a: (_ for _ in ()).throw(OSError()),
])
def test_claude_provider_failures_raise_provider_error(runner):
    with pytest.raises(ProviderError):
        ClaudeCodeProvider(runner=runner).parse("x", {})


def test_qwen_provider_names_model_and_parses():
    calls = []

    def http(url, payload, timeout):
        calls.append(url)
        if url.endswith("/models"):
            return {"data": [{"id": "qwen-local"}]}
        return {"choices": [{"message": {"content": '{"intent": "help"}'}}]}
    p = QwenProvider("http://127.0.0.1:8090/v1/", http=http)
    assert p.parse("x", {}) == {"intent": "help"} and p.model == "qwen-local"
    assert calls[1] == "http://127.0.0.1:8090/v1/chat/completions"
    with pytest.raises(ProviderError):
        QwenProvider(http=lambda *a: (_ for _ in ()).throw(OSError())).parse("x", {})


def test_service_env_consent_and_chain():
    off = NLService.from_env({})
    assert not off.enabled
    s = NLService.from_env({"SEBASTIAN_NL": "claude", "SEBASTIAN_NL_DAILY_CAP": "x"})
    assert [p.name for p in s.chain] == ["claude-code", "qwen"] and not s.enabled and s.daily_cap == 60  # no consent flag
    assert NLService.from_env({"SEBASTIAN_NL": "claude", "SEBASTIAN_NL_CONSENT": "1", "SEBASTIAN_NL_DAILY_CAP": "5"}).daily_cap == 5
    assert NLService.from_env({"SEBASTIAN_NL": "qwen", "SEBASTIAN_NL_CONSENT": "1"}).enabled


def test_service_falls_through_providers_but_stops_on_schema_error():
    class P:
        def __init__(self, name, out=None, exc=None):
            self.name, self.model, self.out, self.exc = name, "m", out, exc

        def parse(self, m, c):
            if self.exc:
                raise self.exc
            return self.out
    ok = P("qwen", {"intent": "help"})
    s = NLService("claude", {"claude": P("claude-code", exc=ProviderError("x")), "qwen": ok}, True)
    assert s.parse("merhaba", {}, TODAY).provider == "qwen"
    s = NLService("claude", {"claude": P("claude-code", {"intent": "nope"}), "qwen": ok}, True)
    r = s.parse("merhaba", {}, TODAY)
    assert r.intent is None and r.schema_error and r.provider == "claude-code"
    s = NLService("claude", {"claude": P("a", exc=ValueError()), "qwen": P("b", exc=ValueError())}, True)
    assert s.parse("x", {}, TODAY).provider == "rules"
