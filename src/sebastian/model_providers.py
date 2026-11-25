"""Model provider interface (roadmap step 4) and the API-key backends.

Contract (shared by every provider, including `nl.ClaudeCodeProvider` and `nl.QwenProvider`):
`name: str`, `model: str`, `parse(message, context) -> dict` (raw model JSON, validated later by `nl.validate`);
raise `nl.ProviderError` on any failure.

Keys are read from the environment only (ANTHROPIC_API_KEY, OPENAI_API_KEY), held in memory, and never logged,
printed or put in an exception message.
"""
from __future__ import annotations
import json
import os
import time
import urllib.request

from . import nl
from .nl import ProviderError, build_prompt, extract_json

KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
API_TIMEOUT = 25


def key_from_env(kind: str, env=None) -> str:
    """The configured key for `kind`, stripped; '' when absent. Environment only, by design."""
    env = os.environ if env is None else env
    return str(env.get(KEY_ENV[kind], "")).strip()


def _post(url: str, headers: dict, payload: dict, timeout: int) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # fixed https API hosts (or an explicit base URL the owner sets)
        return json.loads(r.read().decode())


def _prompt(context: dict) -> str:
    return nl.DIALOGUE_PROMPT if context.get("dialogue") else nl.SYSTEM_PROMPT


class _ApiProvider:
    kind = ""
    default_model = ""
    model_env = ""
    base_env = ""
    default_base = ""

    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None,
                 http=_post, timeout: int = API_TIMEOUT, env=None):
        env = os.environ if env is None else env
        self._key = (api_key if api_key is not None else key_from_env(self.kind, env)).strip()
        self.model = model or env.get(self.model_env) or self.default_model
        self.base = (base_url or env.get(self.base_env) or self.default_base).rstrip("/")
        self.http, self.timeout = http, timeout

    def __repr__(self) -> str:                       # never leak the key through logging or tracebacks
        return f"<{type(self).__name__} model={self.model} key={'set' if self._key else 'missing'}>"

    def request(self, system: str, user: str) -> tuple[str, dict, dict]:
        raise NotImplementedError

    def extract(self, reply: dict) -> str:
        raise NotImplementedError

    def parse(self, message: str, context: dict) -> dict:
        if not self._key:
            raise ProviderError(f"{KEY_ENV[self.kind]} is not set")
        limit = max(1, int(min(self.timeout, getattr(self, "budget", self.timeout))))
        url, headers, payload = self.request(_prompt(context), build_prompt(message, context))
        try:
            reply = self.http(url, headers, payload, limit)
        except Exception as exc:  # noqa: BLE001 - never include the exception text: it may echo request headers
            code = getattr(exc, "code", None)
            reason = "usage" if code == 429 else "network" if isinstance(exc, (OSError, TimeoutError)) else "other"
            raise ProviderError(f"{self.kind} error{f' (http {code})' if code else ''}", reason=reason) from None
        try:
            return extract_json(self.extract(reply))
        except ProviderError:
            raise
        except Exception:  # noqa: BLE001
            raise ProviderError(f"{self.kind} bad reply") from None


class AnthropicProvider(_ApiProvider):
    """Anthropic Messages API with an API key (ANTHROPIC_API_KEY)."""
    name, kind = "anthropic", "anthropic"
    default_model, model_env = "claude-sonnet-4-5", "SEBASTIAN_NL_MODEL"
    base_env, default_base = "ANTHROPIC_BASE_URL", "https://api.anthropic.com"

    def request(self, system, user):
        return (self.base + "/v1/messages",
                {"x-api-key": self._key, "anthropic-version": "2023-06-01"},
                {"model": self.model, "max_tokens": 1000, "temperature": 0, "system": system,
                 "messages": [{"role": "user", "content": user}]})

    def extract(self, reply):
        return "".join(b.get("text", "") for b in reply["content"] if b.get("type") == "text")


class OpenAIProvider(_ApiProvider):
    """OpenAI Chat Completions with an API key (OPENAI_API_KEY)."""
    name, kind = "openai", "openai"
    default_model, model_env = "gpt-4o-mini", "SEBASTIAN_NL_MODEL"
    base_env, default_base = "OPENAI_BASE_URL", "https://api.openai.com/v1"

    def request(self, system, user):
        return (self.base + "/chat/completions", {"Authorization": f"Bearer {self._key}"},
                {"model": self.model, "temperature": 0, "max_tokens": 1000,
                 "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})

    def extract(self, reply):
        return reply["choices"][0]["message"]["content"]


API_PROVIDERS = {"anthropic": AnthropicProvider, "openai": OpenAIProvider}


def selected_kind(env=None) -> str:
    """SEBASTIAN_NL value: off | claude (CLI) | anthropic | openai | qwen | agent."""
    env = os.environ if env is None else env
    return env.get("SEBASTIAN_NL", "off").strip().lower()
