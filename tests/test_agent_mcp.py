"""ADR 0019: the stdlib MCP stdio server forwards to the bot over a private unix socket; the lock-down flags of the real
runner; the stream-json parser. No model, no network."""
import json
import os
import subprocess
import sys

from agent_support import NOW, OWNER, ev, make
from sebastian import agent as agentmod
from sebastian.agent import ClaudeRunner, RunRequest, parse_stream
from sebastian.agent_bridge import Bridge
from sebastian.agent_tools import TOOL_NAMES, AgentTools

SERVER = agentmod.MCP_SCRIPT


def spawn(env_extra):
    return subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, env={**os.environ, **env_extra})


def rpc(p, i, method, params=None):
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}) + "\n")
    p.stdin.flush()
    return json.loads(p.stdout.readline())


def test_server_lists_exactly_the_tools_and_calls_through_the_bridge():
    a, hub, st, clock, _ = make([ev("Toplantı", 20, 14, eid="e1")])
    with Bridge(AgentTools(a)) as br:
        p = spawn(br.env())
        try:
            init = rpc(p, 1, "initialize", {"protocolVersion": "2025-06-18"})
            assert init["result"]["serverInfo"]["name"] == "sebastian"
            p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            listed = rpc(p, 2, "tools/list")["result"]["tools"]
            assert {t["name"] for t in listed} == set(TOOL_NAMES) and all(t["inputSchema"]["type"] == "object" for t in listed)
            r = rpc(p, 3, "tools/call", {"name": "calendar_range", "arguments": {"start_date": "2026-11-19", "end_date": "2026-11-21"}})
            assert r["result"]["isError"] is False and "[e1]" in r["result"]["content"][0]["text"]
            r = rpc(p, 4, "tools/call", {"name": "propose_add_event", "arguments": {"title": "A", "date": "2026-11-21", "time": "09:00"}})
            assert "DRAFT v" in r["result"]["content"][0]["text"] and st.open_pending(OWNER) is not None
            r = rpc(p, 5, "tools/call", {"name": "commit_draft", "arguments": {"draft_version": st.open_pending(OWNER)["id"]}})
            assert r["result"]["isError"] is True and "NOT_CONFIRMED" in r["result"]["content"][0]["text"]
            assert hub.events(NOW, NOW.replace(year=2027)) == [hub._events[0]]
            assert rpc(p, 6, "bogus/method")["error"]["code"] == -32601
        finally:
            p.kill()


def test_a_wrong_secret_is_forbidden_and_the_socket_is_private():
    a, *_ = make()
    with Bridge(AgentTools(a)) as br:
        assert oct(os.stat(br.path).st_mode & 0o777) == "0o600" and oct(os.stat(br.dir).st_mode & 0o777) == "0o700"
        p = spawn({**br.env(), "SEBASTIAN_BRIDGE_TOKEN": "wrong"})
        try:
            rpc(p, 1, "initialize")
            r = rpc(p, 2, "tools/call", {"name": "task_list", "arguments": {}})
            assert r["result"]["isError"] is True and "FORBIDDEN" in r["result"]["content"][0]["text"]
            assert rpc(p, 3, "tools/list")["result"]["tools"] == []
        finally:
            p.kill()
    assert not os.path.exists(br.dir)                           # the temp directory is removed with the turn


def test_server_without_a_bridge_reports_an_error_result_not_a_crash():
    p = spawn({"SEBASTIAN_BRIDGE_SOCK": "/nonexistent/sock", "SEBASTIAN_BRIDGE_TOKEN": "x"})
    try:
        rpc(p, 1, "initialize")
        r = rpc(p, 2, "tools/call", {"name": "task_list", "arguments": {}})
        assert r["result"]["isError"] is True and "BRIDGE_UNAVAILABLE" in r["result"]["content"][0]["text"]
    finally:
        p.kill()


def test_real_runner_command_is_locked_down(tmp_path):
    a, *_ = make()
    seen = {}

    def proc(cmd, inp, cwd, env, timeout):
        seen.update(cmd=cmd, inp=inp, cwd=cwd, env=env, timeout=timeout)
        return 0, json.dumps({"type": "result", "result": "tamam", "is_error": False})
    os.environ["TELEGRAM_BOT_TOKEN_X"] = "secret"
    try:
        runner = ClaudeRunner("sonnet", tmp_path / "cwd", proc=proc)
        res = runner.run(RunRequest("hello", "SYSTEM TEXT", "11111111-1111-1111-1111-111111111111", True, AgentTools(a)))
    finally:
        del os.environ["TELEGRAM_BOT_TOKEN_X"]
    cmd = seen["cmd"]
    assert res.text == "tamam" and res.error is None and seen["inp"] == "hello"
    for flag in ("--strict-mcp-config", "--disable-slash-commands"):
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == "" and cmd[cmd.index("--setting-sources") + 1] == ""
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__sebastian" and cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert "--no-session-persistence" not in cmd and cmd[cmd.index("--session-id") + 1].startswith("1111")
    assert cmd[cmd.index("--system-prompt") + 1] == "SYSTEM TEXT"
    cfg = json.loads(cmd[cmd.index("--mcp-config") + 1])["mcpServers"]
    assert list(cfg) == ["sebastian"] and set(cfg["sebastian"]["env"]) == {"SEBASTIAN_BRIDGE_SOCK", "SEBASTIAN_BRIDGE_TOKEN"}
    assert "chat" not in json.dumps(cfg).lower().replace("sebastian-br", "")      # no chat id anywhere in the config
    assert seen["cwd"] == str(tmp_path / "cwd") and os.listdir(seen["cwd"]) == []
    assert not any("TELEGRAM" in k for k in seen["env"]) and seen["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    runner.proc = proc
    runner.run(RunRequest("again", "S", "22222222-2222-2222-2222-222222222222", False, AgentTools(a)))
    assert "--resume" in seen["cmd"] and "--session-id" not in seen["cmd"]


def test_stream_parser_reads_init_tools_calls_and_errors():
    lines = [{"type": "system", "subtype": "init", "tools": ["mcp__sebastian__task_list"]},
             {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__sebastian__task_list"}, {"type": "text", "text": "x"}]}},
             {"type": "result", "result": "Emredersiniz efendim.", "is_error": False, "total_cost_usd": 0.01}]
    r = parse_stream(0, "\n".join(json.dumps(x) for x in lines) + "\nnot json")
    assert r.init_tools == ["mcp__sebastian__task_list"] and r.tool_calls == ["task_list"] and r.text.startswith("Emredersiniz") and r.error is None
    assert parse_stream(1, json.dumps({"type": "result", "result": "x", "is_error": True})).error == "claude error"
    assert parse_stream(0, "").error.startswith("no result")


def test_runner_timeout_and_missing_binary_are_errors_not_exceptions(tmp_path):
    a, *_ = make()

    def slow(*args):
        raise subprocess.TimeoutExpired("claude", 1)

    def missing(*args):
        raise FileNotFoundError("claude")
    req = RunRequest("x", "s", "u", True, AgentTools(a))
    assert ClaudeRunner(cwd=tmp_path, proc=slow).run(req).error == "timeout"
    assert ClaudeRunner(cwd=tmp_path, proc=missing).run(req).error == "cannot run claude"
