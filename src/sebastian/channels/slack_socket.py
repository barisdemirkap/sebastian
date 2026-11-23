"""Real Socket Mode transport for `SlackChannel` (the one piece that needs the optional `websockets` package: `pip install websockets`).

Not exercised by the offline tests (no network in tests); the translation logic it feeds is. Needs an app-level token (xapp-, scope
connections:write) and a bot token (xoxb-) with im:history, im:write, chat:write and files:read; both live outside the repository."""
from __future__ import annotations
import json
import queue
import threading
import urllib.request


class SocketModeEvents:
    """Callable returning the envelopes received since the last call. Reconnects when Slack asks (disconnect) or the socket drops."""

    def __init__(self, app_token: str):
        self.app_token, self._q, self._stop = app_token, queue.Queue(), threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="slack-socket")
        self._thread.start()

    def _url(self) -> str:
        req = urllib.request.Request("https://slack.com/api/apps.connections.open", data=b"", method="POST",
                                     headers={"Authorization": f"Bearer {self.app_token}", "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 - slack.com only
            res = json.load(r)
        if not res.get("ok"):
            raise RuntimeError(f"slack apps.connections.open failed: {res.get('error', 'unknown')}")
        return res["url"]

    def _run(self) -> None:
        from websockets.sync.client import connect                    # optional dependency, imported only when Slack is used
        while not self._stop.is_set():
            try:
                with connect(self._url()) as ws:
                    for raw in ws:
                        env = json.loads(raw)
                        if env.get("envelope_id"):
                            ws.send(json.dumps({"envelope_id": env["envelope_id"]}))     # acknowledge within 3 s, before any handling
                        if env.get("type") == "disconnect":
                            break
                        if env.get("type") in ("events_api", "interactive"):
                            self._q.put(env)
            except Exception:  # noqa: BLE001 - network blips are expected; back off and reconnect
                self._stop.wait(5)

    def __call__(self) -> list[dict]:
        out = []
        try:
            out.append(self._q.get(timeout=1))                         # long-poll style wait keeps the bot loop from spinning
            while True:
                out.append(self._q.get_nowait())
        except queue.Empty:
            pass
        return out

    def close(self) -> None:
        self._stop.set()
