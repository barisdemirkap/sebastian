"""Local bridge (ADR 0019): serves the agent tools to the MCP stdio process over a unix socket for the duration of ONE
model turn. The socket lives in a private temp directory (0700, socket 0600) and every request must carry the per-turn
secret; the bot process keeps the calendar, the store and the owner binding, the MCP child holds none of them."""
from __future__ import annotations
import hmac
import json
import os
import secrets
import shutil
import socketserver
import tempfile
import threading

from .agent_tools import TOOL_SPECS, AgentTools


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        srv = self.server
        try:
            req = json.loads(self.rfile.readline(1_000_000).decode() or "{}")
        except ValueError:
            req = {}
        if not isinstance(req, dict) or not hmac.compare_digest(str(req.get("token", "")), srv.token):
            out = {"text": "FORBIDDEN", "isError": True}
        elif req.get("op") == "list":
            out = {"tools": [{"name": n, "description": s["description"], "inputSchema": s["inputSchema"]} for n, s in TOOL_SPECS.items()]}
        elif req.get("op") == "call":
            with srv.lock:                       # one tool at a time: the bot thread is blocked on the model meanwhile
                text, err = srv.tools.call(str(req.get("name", "")), req.get("arguments"))
            out = {"text": text, "isError": err}
        else:
            out = {"text": "BAD_REQUEST", "isError": True}
        self.wfile.write((json.dumps(out, ensure_ascii=False) + "\n").encode())


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class Bridge:
    def __init__(self, tools: AgentTools):
        self.tools = tools
        self.token = secrets.token_hex(16)
        self.dir = tempfile.mkdtemp(prefix="sebastian-br-")
        self.path = os.path.join(self.dir, "s")
        self._srv: _Server | None = None

    def __enter__(self) -> "Bridge":
        self._srv = _Server(self.path, _Handler)
        self._srv.tools, self._srv.token, self._srv.lock = self.tools, self.token, threading.Lock()
        os.chmod(self.path, 0o600)
        threading.Thread(target=self._srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        if self._srv is not None:
            self._srv.shutdown()
            self._srv.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def env(self) -> dict:
        return {"SEBASTIAN_BRIDGE_SOCK": self.path, "SEBASTIAN_BRIDGE_TOKEN": self.token}
