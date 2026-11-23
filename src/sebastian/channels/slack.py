"""Slack channel (ADR 0002): a direct-message conversation with the owner over Socket Mode (no public URL, runs on your machine).

This module only translates between Slack payloads and the Channel contract; the two edges are injectable so tests use fakes:
- `call(method, payload) -> dict`: a Slack Web API call (the real one is `web_api_caller(bot_token)`, plain urllib);
- `events() -> list[dict]`: Socket Mode envelopes received since the last call (the real source needs the optional `websockets`
  package and lives in `slack_socket.py`, not imported here).

Identity: the bot's virtual owner chat id (an int, from the config) stands for the Slack user id allowlisted as `owner_user`;
events from anyone else, and non-DM channels, are dropped. Slack ids are strings, so message ids are the message timestamp
in milliseconds and a small table maps them back."""
from __future__ import annotations
import json
import urllib.request
from typing import Callable

MAX_SECTION = 2900            # Slack truncates section text at 3000 characters


def web_api_caller(bot_token: str, timeout: int = 15) -> Callable[[str, dict], dict]:
    def call(method: str, payload: dict) -> dict:
        req = urllib.request.Request(f"https://slack.com/api/{method}", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {bot_token}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - slack.com only
            res = json.load(r)
        if not res.get("ok"):
            raise RuntimeError(f"slack {method} failed: {res.get('error', 'unknown')}")
        return res
    return call


def split_text(text: str, limit: int = MAX_SECTION) -> list[str]:
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit and cur:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    return out + [cur] if cur or not out else out


def blocks_for(text: str, reply_markup: dict | None = None) -> list[dict]:
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    buttons = [{"type": "button", "text": {"type": "plain_text", "text": b["text"][:75]}, "action_id": b["callback_data"], "value": b["callback_data"]}
               for row in (reply_markup or {}).get("inline_keyboard", []) for b in row]
    for i in range(0, len(buttons), 5):                            # an actions block holds at most 25; rows of 5 read better
        blocks.append({"type": "actions", "elements": buttons[i:i + 5]})
    return blocks


class SlackChannel:
    def __init__(self, owner_chat: int, owner_user: str, call: Callable[[str, dict], dict], events: Callable[[], list[dict]],
                 download: Callable[[str], bytes] | None = None):
        self.owner_chat, self.owner_user, self._call, self._events, self._download = owner_chat, owner_user, call, events, download
        self._dm: str | None = None                        # the owner's DM channel id, opened lazily
        self._ts: dict[int, str] = {}                      # message id (ms) -> Slack ts
        self._text: dict[int, str] = {}                    # message id -> last text, so a keyboard can be removed with chat.update
        self._seq = 0
        self._last_uid = 0

    # -- ids
    @staticmethod
    def _mid(ts: str) -> int:
        return int(float(ts) * 1000)

    def _uid(self, ms: int) -> int:
        """Strictly increasing update ids (the bot keeps the last one it handled and ignores anything not newer)."""
        self._seq += 1
        self._last_uid = max(self._last_uid + 1, ms)
        return self._last_uid

    def _remember(self, ts: str, text: str = "") -> int:
        mid = self._mid(ts)
        self._ts[mid] = ts
        if text:
            self._text[mid] = text
        return mid

    def _channel(self) -> str:
        if self._dm is None:
            self._dm = self._call("conversations.open", {"users": self.owner_user})["channel"]["id"]
        return self._dm

    # -- inbound
    def get_updates(self, offset: int | None, timeout: int = 0) -> list[dict]:
        out = []
        for env in self._events():
            u = self._translate(env)
            if u and (offset is None or u["update_id"] >= offset):
                out.append(u)
        return out

    def _translate(self, env: dict) -> dict | None:
        p = env.get("payload") or {}
        chat = {"id": self.owner_chat, "type": "private"}
        if env.get("type") == "events_api":
            e = p.get("event") or {}
            if e.get("type") != "message" or e.get("subtype") or e.get("bot_id") or e.get("channel_type") != "im" or e.get("user") != self.owner_user:
                return None
            if self._dm is None:
                self._dm = e.get("channel")
            mid = self._remember(e["ts"])
            msg = {"message_id": mid, "chat": chat, "from": {"id": self.owner_chat}, "date": int(float(e["ts"]))}
            files = [f for f in e.get("files") or [] if str(f.get("mimetype", "")).startswith("audio")]
            if files:
                msg["voice"] = {"file_id": files[0].get("url_private_download") or files[0].get("url_private", "")}
            else:
                msg["text"] = e.get("text", "")
            return {"update_id": self._uid(mid), "message": msg}
        if env.get("type") == "interactive" and p.get("type") == "block_actions":
            if (p.get("user") or {}).get("id") != self.owner_user or not p.get("actions"):
                return None
            act = p["actions"][0]
            mid = self._remember((p.get("container") or {}).get("message_ts") or (p.get("message") or {}).get("ts") or "0")
            return {"update_id": self._uid(self._mid(act.get("action_ts") or "0")),
                    "callback_query": {"id": p.get("trigger_id") or str(self._seq), "from": {"id": self.owner_chat}, "data": act.get("value") or act["action_id"],
                                       "message": {"message_id": mid, "chat": chat}}}
        return None

    # -- outbound
    def send(self, chat_id: int, text: str, reply_markup: dict | None = None) -> int | None:
        chunks, mid = split_text(text), None
        for i, chunk in enumerate(chunks):
            kb = reply_markup if i == len(chunks) - 1 else None
            res = self._call("chat.postMessage", {"channel": self._channel(), "text": chunk, "blocks": blocks_for(chunk, kb)})
            mid = self._remember(res["ts"], chunk)
        return mid

    def edit_message(self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> None:
        self._call("chat.update", {"channel": self._channel(), "ts": self._ts[message_id], "text": text, "blocks": blocks_for(text, reply_markup)})
        self._text[message_id] = text

    def remove_keyboard(self, chat_id: int, message_id: int) -> None:
        if message_id in self._ts:
            self.edit_message(chat_id, message_id, self._text.get(message_id, " "), None)

    def answer_callback(self, callback_id: str, text: str = "", alert: bool = False) -> None:
        pass                                               # Socket Mode envelopes are acknowledged by the transport; a popup is not a Slack concept

    def send_typing(self, chat_id: int) -> None:
        pass                                               # Slack bots have no typing indicator for DMs

    def get_file(self, file_id: str) -> bytes:
        if self._download is None:
            raise RuntimeError("no file download configured")
        return self._download(file_id)

    def send_voice(self, chat_id: int, ogg: bytes) -> None:
        raise RuntimeError("voice notes are not supported on Slack")
