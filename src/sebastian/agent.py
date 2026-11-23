"""Agent mode (ADR 0019): a persistent `claude -p --resume` session for the owner chat, Sebastian's typed tools exposed through
a stdlib MCP server, and a gate in CODE around every write.

The model understands and talks; code decides what is written. Previews are rendered by code. `commit_draft` works only
for a single-use grant that CODE mints for the exact current draft version after the owner's latest message was a pure
affirmative AND the last bot message was the code-rendered preview of that same version. Fallback chain: agent ->
dialogue manager (rules + one-shot) -> read-only mode; circuit breaker and per-day cap. Rollback: change SEBASTIAN_NL, or
`/ajan kapat` (instant, no restart, nothing lost: the draft is an ordinary pending action both modes understand)."""
from __future__ import annotations
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import dialogue, i18n
from .agent_bridge import Bridge
from .agent_tools import TOOL_NAMES, TOOL_SPECS, AgentTools
from .merge import merge
from .store import Store
from .textutil import clean, fold

PROMPT_VERSION = hashlib.sha256((i18n.AGENT_SYSTEM + json.dumps(TOOL_SPECS, sort_keys=True)).encode()).hexdigest()[:8]
EXPECTED_INIT_TOOLS = frozenset("mcp__sebastian__" + n for n in TOOL_NAMES)
MCP_SCRIPT = Path(__file__).with_name("mcp_server.py")
DEFAULT_MODEL, DEFAULT_CAP, DEFAULT_TURNS, DEFAULT_TIMEOUT = "sonnet", 80, 40, 90
IDLE_ROTATE = timedelta(hours=6)
BREAKER_FAILURES, BREAKER_OPEN = 3, timedelta(minutes=10)
CONTEXT_DAYS = 7
# first-person claims of a calendar write; without a real write in the same turn they are replaced (never trust "kaydettim")
CLAIM = re.compile(r"\b(kaydettim|kaydedildi|ekledim|eklendi|sildim|silindi|isledim|islendi|degistirdim|guncelledim|tasidim)\b")
SCRUB = ("TELEGRAM", "TOKEN", "SECRET", "GOOGLE")


# ---------------------------------------------------------------------------------------------------- runners (edges)
@dataclass
class RunRequest:
    text: str                      # the whole user message of this turn (system line + context + the owner's words)
    system: str
    session_id: str
    first: bool
    tools: AgentTools
    timeout: int = DEFAULT_TIMEOUT


@dataclass
class RunResult:
    text: str = ""
    init_tools: list[str] | None = None
    error: str | None = None
    tool_calls: list[str] = field(default_factory=list)
    cost: float | None = None


def _proc(cmd, input_text, cwd, env, timeout):
    p = subprocess.run(cmd, input=input_text, capture_output=True, text=True, cwd=cwd, env=env, timeout=timeout)
    return p.returncode, p.stdout


class ClaudeRunner:
    """The real edge: one `claude -p` process per turn, locked down, resuming the owner's session."""
    needs_bridge = True

    def __init__(self, model: str = DEFAULT_MODEL, cwd: str | Path | None = None, proc=_proc, binary: str = "claude",
                 max_turns: int = 10):
        self.model, self.proc, self.binary, self.max_turns = model, proc, binary, max_turns
        self.cwd = Path(cwd) if cwd else Path.home() / ".config" / "sebastian" / "agent-cwd"

    def command(self, req: RunRequest, bridge_env: dict) -> list[str]:
        mcp = {"mcpServers": {"sebastian": {"command": sys.executable, "args": [str(MCP_SCRIPT)], "env": bridge_env}}}
        cmd = [self.binary, "-p", "--model", self.model, "--tools", "", "--strict-mcp-config", "--mcp-config", json.dumps(mcp),
               "--allowedTools", "mcp__sebastian", "--permission-mode", "dontAsk", "--setting-sources", "",
               "--disable-slash-commands", "--system-prompt", req.system, "--output-format", "stream-json", "--verbose",
               "--max-turns", str(self.max_turns)]
        # --no-session-persistence is NOT used: --resume needs the transcript. Retention: see ADR 0019 (cleanupPeriodDays).
        return cmd + (["--session-id", req.session_id] if req.first else ["--resume", req.session_id])

    def run(self, req: RunRequest) -> RunResult:
        env = {k: v for k, v in os.environ.items() if not any(x in k.upper() for x in SCRUB)}
        env.update({"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"})
        self.cwd.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with Bridge(req.tools) as br:
                rc, out = self.proc(self.command(req, br.env()), req.text, str(self.cwd), env, req.timeout)
        except subprocess.TimeoutExpired:
            return RunResult(error="timeout")
        except OSError:
            return RunResult(error="cannot run claude")
        return parse_stream(rc, out)


def parse_stream(rc: int, out: str) -> RunResult:
    """Read the stream-json events: the init event (tools), tool_use names, the final result."""
    res = RunResult()
    final = None
    for ln in (out or "").splitlines():
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            res.init_tools = [str(x) for x in ev.get("tools") or []]
        elif ev.get("type") == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    res.tool_calls.append(str(b.get("name", "")).replace("mcp__sebastian__", ""))
        elif ev.get("type") == "result":
            final = ev
    if final is None:
        res.error = f"no result (rc={rc})"
    elif rc != 0 or final.get("is_error"):
        res.error = "claude error"
    else:
        res.text = str(final.get("result") or "")
        res.cost = final.get("total_cost_usd")
    return res


# ---------------------------------------------------------------------------------------------------- the mode
class AgentMode:
    def __init__(self, assistant, runner, daily_cap: int = DEFAULT_CAP, max_turns: int = DEFAULT_TURNS,
                 idle: timedelta = IDLE_ROTATE, timeout: int = DEFAULT_TIMEOUT):
        self.a, self.runner = assistant, runner
        self.daily_cap, self.max_turns, self.idle, self.timeout = daily_cap, max_turns, idle, timeout
        self.disabled_reason: str | None = None

    @classmethod
    def from_env(cls, assistant, env=None) -> "AgentMode | None":
        """SEBASTIAN_NL=agent plus the owner's consent (SEBASTIAN_NL_CONSENT=1); anything else leaves agent mode out entirely."""
        env = os.environ if env is None else env
        if env.get("SEBASTIAN_NL", "").strip().lower() != "agent" or env.get("SEBASTIAN_NL_CONSENT") != "1":
            return None

        def num(name: str, default: int) -> int:
            try:
                return max(1, int(env.get(name, default)))
            except ValueError:
                return default
        runner = ClaudeRunner(env.get("SEBASTIAN_AGENT_MODEL") or DEFAULT_MODEL, env.get("SEBASTIAN_AGENT_CWD") or None)
        mode = cls(assistant, runner, num("SEBASTIAN_AGENT_DAILY_CAP", DEFAULT_CAP), num("SEBASTIAN_AGENT_MAX_TURNS", DEFAULT_TURNS),
                   timeout=num("SEBASTIAN_AGENT_TIMEOUT", DEFAULT_TIMEOUT))
        problems = self_test()
        if problems:
            mode.disabled_reason = "; ".join(problems)
        return mode

    # ------------------------------------------------------------------ switches
    @property
    def store(self) -> Store:
        return self.a.store

    def switched_on(self) -> bool:
        return self.store.get_meta("agent_off", "0") != "1"

    def set_switch(self, on: bool) -> None:
        self.store.set_meta("agent_off", "0" if on else "1")

    def _cap_key(self, now: datetime) -> str:
        return "agent:" + now.date().isoformat()

    def active(self, now: datetime) -> bool:
        if self.disabled_reason or not self.switched_on():
            return False
        until = self.store.get_meta("agent_open_until")
        if until and now < datetime.fromisoformat(until):
            return False                                           # circuit breaker is open
        return self.store.nl_calls(self._cap_key(now), self.a.owner) < self.daily_cap

    def _failed(self, now: datetime, why: str) -> None:
        n = int(self.store.get_meta("agent_fail_n", "0") or 0) + 1
        self.store.set_meta("agent_fail_n", n)
        self.store.audit("agent_failed", {"why": why[:60], "n": n}, now, self.a.owner)
        self.a._flag("S5", "agent-" + why[:30])
        self.store.agent_drop_session(self.a.owner)                # a half-made session is never resumed
        if n >= BREAKER_FAILURES:
            self.store.set_meta("agent_open_until", (now + BREAKER_OPEN).isoformat(timespec="seconds"))
            self.store.set_meta("agent_fail_n", 0)

    def _succeeded(self) -> None:
        self.store.set_meta("agent_fail_n", 0)

    # ------------------------------------------------------------------ session
    def _session(self, now: datetime) -> tuple[str, bool]:
        """(session id, is_new). A new session starts when none exists, the prompt version changed, the session is old
        (turns or idle time). Facts survive because they live in the DB (notes, lists, tasks, draft), not in the session."""
        st = self.store
        row = st.agent_session(self.a.owner)
        if (row is None or row["prompt_version"] != PROMPT_VERSION or row["turns"] >= self.max_turns
                or now - datetime.fromisoformat(row["last_at"]) > self.idle):
            sid = str(uuid.uuid4())
            st.agent_start_session(self.a.owner, sid, PROMPT_VERSION, now)
            return sid, True
        return row["session_id"], False

    # ------------------------------------------------------------------ grant (CODE only)
    def mint_grant(self, row, text: str, now: datetime) -> bool:
        """A single-use grant for the exact current draft version. Conditions, all in code: the owner's latest message is a
        pure affirmative (a delete needs the stricter 'evet sil' / 'sil' / approval); the last bot message of the log
        contains the code-rendered preview of THIS version (any other bot message, and any edit, kills it)."""
        delete = row["intent"] == "delete_event"
        if dialogue.parse_confirm(text, delete) != "yes" or Store.pending_state(row, now) != "ok":
            return False
        shown = self.store.get_meta("shown_preview")
        try:
            sp = json.loads(shown) if shown else {}
        except ValueError:
            sp = {}
        if sp.get("pid") != row["id"] or sp.get("hash") != row["args_hash"] or not sp.get("text"):
            return False
        outs = [m for m in self.store.recent_messages(self.a.owner, 8) if m["direction"] == "out"]
        if not outs or sp["text"] not in outs[-1]["text"]:
            return False
        self.store.grant_mint(self.a.owner, row["id"], row["args_hash"], now)
        self.store.audit("agent_grant", {"pending": row["id"]}, now, self.a.owner)
        return True

    # ------------------------------------------------------------------ context built by code
    def context(self, now: datetime, new_session: bool, draft_row) -> str:
        a, st = self.a, self.store
        lines = [f"NOW: {now:%Y-%m-%d} {i18n.DAYS[now.weekday()]} {now:%H:%M} ({a.tz})", f"EVENTS next {CONTEXT_DAYS} days:"]
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            evs = merge(a.hub.events(start, start + timedelta(days=CONTEXT_DAYS)))
        except Exception:  # noqa: BLE001
            evs = []
            lines.append("(calendar unavailable)")
        tools = AgentTools(a)
        rows = [tools._line(e) for e in evs if e.event_id][:40]
        if lines[-1] != "(calendar unavailable)":
            lines += rows or ["(none)"]
        lines.append("OPEN LISTS:")
        for key, meta in i18n.LISTS.items():
            items = [r["text"] for r in st.list_items(a.owner, key)]
            lines.append(f"{meta['title']} (list={key}): " + ("; ".join(f"{i}. {x}" for i, x in enumerate(items, 1)) or "(empty)"))
        tasks = st.open_tasks(a.owner)
        lines.append("OPEN TASKS: " + ("; ".join(f"{i}. {r['title']}" for i, r in enumerate(tasks[:20], 1)) or "(none)"))
        notes = st.notes(a.owner)
        lines.append("NOTES: " + ("; ".join(f"{r['key']}={r['value']}" for r in notes[:40]) or "(none)"))
        if draft_row is not None:
            lines.append(f"DRAFT v{draft_row['id']} ({draft_row['intent']}):\n{a.render_pending(draft_row)}")
        else:
            lines.append("DRAFT: none")
        if new_session:
            recent = st.recent_messages(a.owner, 7)[:-1]       # the newest row is the message of this turn itself
            if recent:
                lines.append("RECENT MESSAGES (new session):")
                lines += [f"{'OWNER' if m['direction'] == 'in' else 'SEBASTIAN'}: {clean(m['text'], 160)}" for m in recent]
        return "\n".join(lines)

    # ------------------------------------------------------------------ one owner message
    def turn(self, text: str, now: datetime) -> list[str] | None:
        """Replies for the owner message, or None when the caller must fall back to the dialogue manager."""
        a, st = self.a, self.store
        if not self.active(now):
            return None
        row = st.open_pending(a.owner)
        if row is not None and Store.pending_state(row, now) != "ok":
            if dialogue.parse_confirm(text, row["intent"] == "delete_event") in ("yes", "weak"):
                return None                                    # an expired preview that is confirmed: the manager re-shows it
            st.close_pending(row["id"], "expired")
            row = None
        granted = False
        if row is not None:
            delete = row["intent"] == "delete_event"
            decision = dialogue.parse_confirm(text, delete)
            if decision == "no":                               # a pure negative: code cancels, no model call needed
                st.close_pending(row["id"], "cancelled")
                st.grant_revoke_all(a.owner)
                st.set_meta("agent_q", "0")
                return [i18n.t("cancelled")]
            if decision == "weak":                             # a plain yes on a delete: hint, then the preview again
                text_ = a.render_pending(row)
                a.shown = row["id"]
                a.note_preview(row["id"], text_)
                return [i18n.t("need_evet_sil"), text_]
            granted = decision == "yes" and self.mint_grant(row, text, now)
        st.nl_bump(self._cap_key(now), a.owner)
        sid, new = self._session(now)
        tools = AgentTools(a)
        line = i18n.AGENT_LINE.format(today=f"{now:%Y-%m-%d}", time=f"{now:%H:%M}", addr=i18n.address(),
                                      draft=f"v{row['id']}" if row else "yok", ok="EVET" if granted else "YOK",
                                      extra=" yeni_oturum=1" if new else "")
        req = RunRequest(f"{line}\n{self.context(now, new, row)}\n---\n{text}", i18n.AGENT_SYSTEM, sid, new, tools, self.timeout)
        try:
            res = self.runner.run(req)
        except Exception as exc:  # noqa: BLE001 - a runner bug never breaks the chat
            res = RunResult(error=type(exc).__name__)
        if res.error is None and res.init_tools is not None and set(res.init_tools) != EXPECTED_INIT_TOOLS:
            res.error = "init-tools-mismatch"                  # the lock-down failed: nothing the model said is used
        if res.error is not None:
            st.grant_revoke_all(a.owner)
            self._failed(now, res.error)
            return None
        self._succeeded()
        st.agent_touch_session(a.owner, now)
        out = self._compose(res.text, tools, row, granted, now)
        st.grant_revoke_all(a.owner)                           # a grant never outlives its turn
        return out

    # ------------------------------------------------------------------ reply composed by code
    def _compose(self, model_text: str, tools: AgentTools, before_row, granted: bool, now: datetime) -> list[str]:
        a, st = self.a, self.store
        eff = tools.effects
        if granted and not eff.committed and before_row is not None:
            cur = st.open_pending(a.owner)
            if cur is not None and cur["id"] == before_row["id"] and cur["args_hash"] == before_row["args_hash"]:
                tools.t_commit_draft({"draft_version": cur["id"]})        # the model forgot: the owner's yes is honoured by code
        if eff.committed:
            st.set_meta("agent_q", "0")
            a.shown = None
            return list(eff.committed)                         # receipts are code text; the model's words are dropped
        text = self._scrub(model_text)
        if CLAIM.search(fold(text)) and not eff.immediate:
            st.audit("agent_false_claim", {}, now, a.owner)
            a._flag("S5", "agent-false-claim")
            text = ""
        cur = st.open_pending(a.owner)
        replies = [text] if text else []
        if cur is not None and Store.pending_state(cur, now) == "ok":
            shown = a.render_pending(cur)                      # preview LAST, so the owner's next 'evet' qualifies for a grant
            a.shown = cur["id"]
            a.note_preview(cur["id"], shown)
            if granted and not replies:
                replies.append(i18n.t("agent_not_saved"))       # the yes was valid but nothing could be saved: say so
            replies.append(shown)
            st.set_meta("agent_q", "1")
        else:
            st.set_meta("agent_q", "1" if "?" in text else "0")           # an open question: a following 'evet' is answered in context
            if not replies:
                replies.append(i18n.t("oops"))
        return replies

    @staticmethod
    def _scrub(text: str) -> str:
        """The model's words: trimmed, at most 900 characters, and at most one form of address."""
        text = (text or "").strip()[:900]
        return i18n.one_address_loose(text)


# ---------------------------------------------------------------------------------------------------- startup self-test
def self_test() -> list[str]:
    """In-process checks (no model call, temp store): the exact tool set, no chat parameter, and the gate. Returns problems."""
    from zoneinfo import ZoneInfo
    from .assistant import Assistant
    from .nl import NLService
    from .providers import FixtureProvider
    problems: list[str] = []
    if len(TOOL_NAMES) != 19 or any("chat" in k.lower() for s in TOOL_SPECS.values() for k in s["inputSchema"]["properties"]):
        problems.append("tool set")
    tz = ZoneInfo("Europe/Istanbul")
    now = datetime(2030, 1, 7, 10, 0, tzinfo=tz)
    asst = Assistant(FixtureProvider([]), Store(), NLService("off"), tz, 1, lambda: now)
    t = AgentTools(asst)
    text, err = t.call("propose_add_event", {"title": "Selftest", "date": "2030-01-08", "time": "11:00"})
    pid = asst.store.open_pending(1)["id"] if asst.store.open_pending(1) else 0
    if err or not pid:
        return problems + ["draft"]
    if not t.call("commit_draft", {"draft_version": pid})[1] or asst.hub.events(now, now + timedelta(days=3)):
        problems.append("commit without grant")
    mode = AgentMode(asst, None)
    row = asst.store.open_pending(1)
    asst.note_preview(pid, asst.render_pending(row))
    asst.store.log(1, asst.render_pending(row), "out", now)
    if mode.mint_grant(row, "evet ama saati 16 yap", now) or asst.store.grant_live(1):
        problems.append("compound yes granted")
    if not mode.mint_grant(row, "evet", now):
        problems.append("plain yes not granted")
    t.call("edit_draft", {"draft_version": pid, "ops": [{"op": "set", "items": "all", "field": "time", "value": "12:00"}]})
    pid2 = asst.store.open_pending(1)["id"]
    if not t.call("commit_draft", {"draft_version": pid2})[1]:
        problems.append("stale grant honoured")
    return problems
