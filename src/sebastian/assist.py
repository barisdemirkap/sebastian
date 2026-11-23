"""Model assist for statements (ADR 0023): rules first, the model only when the rules cannot read a message with confidence.

The model CHOOSES (an action kind and candidate ids out of a list this code supplies); it never writes a date, a time or a name:
code reads days and clock times from the clause's own words (`clauses._day/_time`), and rejects the WHOLE answer when an id is outside the
supplied list, a span is not a substring of the message, a kind is unknown, or a day is missing for a defer.

Single source of truth: for a clause the model handled, the model's kind is AUTHORITATIVE (the regex overrides on the raw text in
`statements.py` are skipped for it). Rules-confident messages never reach the model, so the two never fight over the same clause.

Modes (SEBASTIAN_NL_ASSIST): off (DEFAULT since the ADR 0023 kill criterion fired) | shadow (the answer is logged and compared with the rules, never acted on) | on (promoted).
It only runs when the NL layer is on with the owner's consent. Every model-derived task write still goes through a preview + 'evet'."""
from __future__ import annotations
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import clauses as clausemod
from .textutil import fold

PROMPT_PATH = Path(__file__).with_name("assist_prompt.md")
DEFAULT_MODEL = "claude-haiku-5-5"            # pinned full id (an alias like 'haiku' moves under us)
TIMEOUT = 10
KINDS = ("done", "defer", "forgot", "delete", "keep", "remind", "plan", "add", "read", "info", "other")
ACTING = {"done", "defer", "forgot", "delete", "keep", "remind", "plan"}
NEEDS_ID = ACTING
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["clauses"], "properties": {"clauses": {
    "type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "object", "additionalProperties": False, "required": ["span", "kind", "ids", "scope"],
        "properties": {"span": {"type": "string"}, "kind": {"type": "string", "enum": list(KINDS)},
                       "ids": {"type": "array", "items": {"type": "string"}}, "scope": {"type": "string", "enum": ["none", "all", "others"]}}}}}}


class AssistError(Exception):
    """The answer was unusable. `reason` is a short machine word that goes to the audit log."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class Candidates:
    tasks: dict = field(default_factory=dict)       # 'T12' -> row
    events: dict = field(default_factory=dict)      # 'E1' -> Event

    def listing(self, tz) -> dict:
        return {"tasks": [{"id": k, "title": r["title"]} for k, r in self.tasks.items()],
                "events": [{"id": k, "title": e.title, "when": f"{e.start.astimezone(tz):%Y-%m-%d %H:%M}"} for k, e in self.events.items()]}


def candidates(tasks: list, events: list) -> Candidates:
    return Candidates({f"T{r['id']}": r for r in tasks}, {f"E{i}": e for i, e in enumerate(events[:30], 1)})


def build_prompt(message: str, cands: Candidates, tz, rules_said: str) -> str:
    return json.dumps({"message": message[:600], **cands.listing(tz), "rules_said": rules_said}, ensure_ascii=False)


def validate(raw, message: str, cands: Candidates, today: date) -> list[dict]:
    """Clause dicts (the shape `clauses.classify` returns, plus 'model': True and 'ids') or AssistError. Nothing outside the schema passes."""
    if not isinstance(raw, dict) or not isinstance(raw.get("clauses"), list) or not 1 <= len(raw["clauses"]) <= 8:
        raise AssistError("schema")
    fm, out = fold(message), []
    msg_day = clausemod._day([clausemod._w(t) for t in message.split()], today)
    for c in raw["clauses"]:
        if not isinstance(c, dict) or set(c) != {"span", "kind", "ids", "scope"} or c["kind"] not in KINDS or c["scope"] not in ("none", "all", "others") \
                or not isinstance(c["span"], str) or not isinstance(c["ids"], list) or not all(isinstance(i, str) for i in c["ids"]):
            raise AssistError("schema")
        if fold(c["span"]).strip() not in fm or not c["span"].strip():
            raise AssistError("span")
        allowed = set(cands.tasks) | set(cands.events)
        if any(i not in allowed for i in c["ids"]):
            raise AssistError("id_outside_list")
        scope = None if c["scope"] == "none" else c["scope"]
        if c["kind"] in NEEDS_ID and not c["ids"] and not scope:
            raise AssistError("no_id")
        sp = c["span"].strip()
        ws = [clausemod._w(t) for t in sp.split()]
        day, tm = clausemod._day(ws, today) or (msg_day if c["kind"] == "defer" else None), clausemod._time(fold(sp))
        if c["kind"] == "defer" and day is None:
            raise AssistError("no_day")
        titles = [cands.tasks[i]["title"] for i in c["ids"] if i in cands.tasks] + [cands.events[i].title for i in c["ids"] if i in cands.events]
        out.append({"raw": sp, "subject": ", ".join(titles), "day": day, "time": tm, "scope": scope, "rest": "", "kind": c["kind"],
                    "model": True, "ids": list(c["ids"])})
    return out


def summarize(clauses: list[dict], key: str = "ids") -> list[list]:
    """[(kind, sorted ids)] for the shadow comparison."""
    return [[c["kind"], sorted(c.get(key) or [])] for c in clauses]


# -- the model edge --------------------------------------------------------------------------------------------------------------------
def _run(cmd, input_text, cwd, env, timeout):
    p = subprocess.run(cmd, input=input_text, capture_output=True, text=True, cwd=cwd, env=env, timeout=timeout)
    return p.returncode, p.stdout


class ClaudeAssistProvider:
    """One-shot `claude -p` with a JSON schema (structured output). No tools, no MCP, no session. The CLI has no temperature flag, so
    determinism comes from the strict schema + code validation; `temperature_note` records that for the audit log."""
    name, temperature_note = "claude-code", "cli-default (no flag)"

    def __init__(self, model: str | None = None, runner=_run, binary: str = "claude", timeout: int = TIMEOUT):
        self.model = model or os.environ.get("SEBASTIAN_NL_ASSIST_MODEL") or DEFAULT_MODEL
        self.runner, self.binary, self.timeout = runner, binary, timeout

    def command(self) -> list[str]:
        return [self.binary, "-p", "--model", self.model, "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--no-session-persistence", "--disable-slash-commands", "--setting-sources", "", "--output-format", "json",
                "--json-schema", json.dumps(SCHEMA), "--system-prompt", PROMPT_PATH.read_text(encoding="utf-8")]

    def call(self, prompt: str) -> tuple[dict, dict]:
        """(structured answer, meta {ms, cost_usd, model, raw}) or AssistError('timeout' | 'cannot_run' | 'envelope' | 'usage' | 'error')."""
        env = {k: v for k, v in os.environ.items() if not any(x in k.upper() for x in ("TELEGRAM", "TOKEN", "SECRET", "GOOGLE"))}
        env.update({"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"})
        t0 = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="sebastian-assist-") as cwd:
                rc, out = self.runner(self.command(), prompt, cwd, env, self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise AssistError("timeout") from exc
        except OSError as exc:
            raise AssistError("cannot_run") from exc
        ms = int((time.monotonic() - t0) * 1000)
        try:
            env_ = json.loads(out)
        except (ValueError, TypeError) as exc:
            raise AssistError("usage" if re.search(r"usage limit|rate limit|quota", str(out), re.I) else "envelope") from exc
        if rc != 0 or not isinstance(env_, dict) or env_.get("is_error"):
            raise AssistError("usage" if re.search(r"usage limit|rate limit|quota|overloaded", str(env_), re.I) else "error")
        raw = env_.get("structured_output")
        if raw is None and isinstance(env_.get("result"), str):
            try:
                raw = json.loads(env_["result"])
            except ValueError:
                raw = None
        model = next(iter(env_.get("modelUsage") or {}), self.model)
        return raw, {"ms": ms, "cost_usd": env_.get("total_cost_usd"), "model": model, "raw": json.dumps(raw, ensure_ascii=False)[:1500]}


class AssistService:
    def __init__(self, mode: str, provider, enabled: bool = True):
        self.mode = mode if mode in ("off", "shadow", "on") else "shadow"
        self.provider, self.allowed = provider, enabled

    @classmethod
    def from_env(cls, env, nl) -> "AssistService":
        enabled = bool(nl.enabled and getattr(nl, "mode", "") == "claude")
        return cls(env.get("SEBASTIAN_NL_ASSIST", "off").strip().lower(), ClaudeAssistProvider(), enabled)

    @property
    def active(self) -> bool:
        return self.allowed and self.mode != "off"
