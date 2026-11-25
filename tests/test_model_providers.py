"""Model provider interface: API-key backends, env-only keys, never logged; doctor key check; container files."""
import re
import stat
from datetime import date
from pathlib import Path
import pytest
from sebastian import cli, nl
from sebastian.model_providers import AnthropicProvider, OpenAIProvider, key_from_env
from sebastian.nl import NLService, ProviderError

ROOT = Path(__file__).parent.parent
KEY = "sk-test-SECRETVALUE123"


def test_key_is_env_only():
    assert key_from_env("anthropic", {"ANTHROPIC_API_KEY": f" {KEY} "}) == KEY
    assert key_from_env("openai", {"ANTHROPIC_API_KEY": KEY}) == ""


def test_anthropic_request_and_parse():
    seen = {}

    def http(url, headers, payload, timeout):
        seen.update(url=url, headers=headers, payload=payload)
        return {"content": [{"type": "text", "text": '```json\n{"intent": "help"}\n```'}]}
    p = AnthropicProvider(api_key=KEY, http=http, env={})
    assert p.parse("hi", {}) == {"intent": "help"}
    assert seen["url"] == "https://api.anthropic.com/v1/messages" and seen["headers"]["x-api-key"] == KEY
    assert seen["payload"]["system"] == nl.SYSTEM_PROMPT
    p.parse("hi", {"dialogue": True})
    assert seen["payload"]["system"] == nl.DIALOGUE_PROMPT


def test_openai_request_and_parse():
    seen = {}

    def http(url, headers, payload, timeout):
        seen.update(url=url, headers=headers, payload=payload)
        return {"choices": [{"message": {"content": '{"intent": "help"}'}}]}
    p = OpenAIProvider(api_key=KEY, http=http, env={"SEBASTIAN_NL_MODEL": "m1"})
    assert p.parse("hi", {}) == {"intent": "help"} and p.model == "m1"
    assert seen["url"].endswith("/chat/completions") and seen["headers"]["Authorization"] == f"Bearer {KEY}"


@pytest.mark.parametrize("cls", [AnthropicProvider, OpenAIProvider])
def test_key_never_leaks(cls):
    def boom(url, headers, payload, timeout):
        raise RuntimeError(f"failed with {headers}")
    p = cls(api_key=KEY, http=boom, env={})
    assert KEY not in repr(p)
    with pytest.raises(ProviderError) as e:
        p.parse("hi", {})
    assert KEY not in str(e.value) and e.value.__cause__ is None and e.value.__suppress_context__
    with pytest.raises(ProviderError, match="not set"):
        cls(api_key="", http=boom, env={}).parse("hi", {})
    bad = cls(api_key=KEY, http=lambda *a: {"nonsense": 1}, env={})
    with pytest.raises(ProviderError):
        bad.parse("hi", {})


def test_rate_limit_is_usage_reason():
    class E(OSError):
        code = 429

    def http(*a):
        raise E("x")
    with pytest.raises(ProviderError) as e:
        OpenAIProvider(api_key=KEY, http=http, env={}).parse("hi", {})
    assert e.value.reason == "usage"


def test_selection_by_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    for mode, cls in (("anthropic", AnthropicProvider), ("openai", OpenAIProvider)):
        svc = NLService.from_env({"SEBASTIAN_NL": mode, "SEBASTIAN_NL_CONSENT": "1"})
        assert isinstance(svc.chain[0], cls) and svc.chain[1].name == "qwen" and svc.enabled
    assert NLService.from_env({"SEBASTIAN_NL": "claude"}).chain[0].name == "claude-code"
    assert NLService.from_env({}).chain == []


def test_service_uses_api_provider_and_consent_gate():
    class Fake:
        name, model = "anthropic", "m"

        def parse(self, message, context):
            return {"intent": "help"}
    svc = NLService("anthropic", {"anthropic": Fake()}, consent=True)
    r = svc.parse("yardim", {}, date(2026, 11, 25))
    assert r.provider == "anthropic" and r.intent["intent"] == "help"
    assert not NLService("anthropic", {"anthropic": Fake()}).enabled


def test_doctor_rows_never_print_key(tmp_path):
    rows = cli._model_rows({"SEBASTIAN_NL": "anthropic", "ANTHROPIC_API_KEY": KEY}, tmp_path)
    assert (f"anthropic api key", True) == rows[0][:2] and "consent" == rows[1][0] and not rows[1][1]
    assert KEY not in repr(rows)
    miss = cli._model_rows({"SEBASTIAN_NL": "openai"}, tmp_path)
    assert miss[0][1] is False
    ph = cli._model_rows({"SEBASTIAN_NL": "openai", "OPENAI_API_KEY": "your-openai-key-here"}, tmp_path)
    assert ph[0][1] is False
    assert cli._model_rows({}, tmp_path)[0][1] is True
    assert cli._model_rows({"SEBASTIAN_NL": "claude"}, tmp_path)[0][1] is True


def test_doctor_flags_open_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text("X=1\n")
    f.chmod(0o644)
    rows = cli._model_rows({}, tmp_path)
    assert rows[-1][0] == ".env permissions" and rows[-1][1] is False
    f.chmod(0o600)
    assert cli._model_rows({}, tmp_path)[-1][1] is True


def test_dockerfile_static_checks():
    t = (ROOT / "Dockerfile").read_text()
    assert re.search(r"^USER sebastian$", t, re.M) and "HEALTHCHECK" in t and "VOLUME /data" in t
    assert "slim" in t and not re.search(r"API_KEY|sk-", t)
    assert t.rstrip().splitlines()[-1].startswith("CMD")
    assert ".env" in (ROOT / ".dockerignore").read_text()


def test_compose_and_env_example_are_safe():
    c = (ROOT / "compose.yaml").read_text()
    assert "sebastian-data:/data" in c and "env_file: .env" in c and "volumes:\n  sebastian-data:" in c
    assert not (ROOT / "docker-compose.yml").exists()
    e = (ROOT / ".env.example").read_text()
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "SEBASTIAN_NL_CONSENT"):
        assert var in e
    assert "your-" in e and not re.search(r"sk-[A-Za-z0-9]{8,}", e)
    assert "\n.env\n" in "\n" + (ROOT / ".gitignore").read_text()


def test_adr_and_quickstart_cover_the_decision():
    t = (ROOT / "docs/adr/0004-isolated-instance-per-user.md").read_text().lower()
    for needle in ("demand check", "threat model", "legal", "operator", "costing", "not built"):
        assert needle in t
    q = (ROOT / "docs/QUICKSTART.md").read_text()
    assert "docker compose up -d" in q and "SEBASTIAN_NL" in q
