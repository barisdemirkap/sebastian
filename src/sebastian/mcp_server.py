#!/usr/bin/env python3
"""Minimal stdlib MCP stdio server (ADR 0019). It holds NO logic and NO data: every tool call is forwarded over a local
unix socket to the bot process, which owns the calendar, the store and the owner binding. The socket path and a
per-turn secret come from the environment, so the model can never address another chat (no tool takes a chat id).
stdout carries nothing but newline-delimited JSON-RPC; diagnostics go to stderr. Imports nothing from sebastian."""
import json
import os
import socket
import sys

SOCK = os.environ.get("SEBASTIAN_BRIDGE_SOCK", "")
TOKEN = os.environ.get("SEBASTIAN_BRIDGE_TOKEN", "")


def bridge(req: dict) -> dict:
    """One request/response over the unix socket (one JSON line each way)."""
    req = {**req, "token": TOKEN}
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(60)
    try:
        s.connect(SOCK)
        s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode() or "{}")
    finally:
        s.close()


def handle(msg: dict):
    m, i = msg.get("method"), msg.get("id")
    if i is None:                                   # a notification (notifications/initialized): no reply
        return None
    if m == "initialize":
        pv = (msg.get("params") or {}).get("protocolVersion", "2025-06-18")
        return {"jsonrpc": "2.0", "id": i, "result": {"protocolVersion": pv, "capabilities": {"tools": {}},
                                                       "serverInfo": {"name": "sebastian", "version": "1"}}}
    if m == "ping":
        return {"jsonrpc": "2.0", "id": i, "result": {}}
    if m == "tools/list":
        try:
            tools = bridge({"op": "list"}).get("tools", [])
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[mcp] bridge list failed: {type(exc).__name__}\n")
            tools = []
        return {"jsonrpc": "2.0", "id": i, "result": {"tools": tools}}
    if m == "tools/call":
        p = msg.get("params") or {}
        try:
            r = bridge({"op": "call", "name": p.get("name"), "arguments": p.get("arguments") or {}})
            text, err = str(r.get("text", "")), bool(r.get("isError", True))
        except Exception as exc:  # noqa: BLE001
            text, err = f"BRIDGE_UNAVAILABLE: {type(exc).__name__}", True
        return {"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": text}], "isError": err}}
    return {"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": "Method not found"}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            out = handle(json.loads(line))
        except Exception as exc:  # noqa: BLE001
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": type(exc).__name__}}
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
