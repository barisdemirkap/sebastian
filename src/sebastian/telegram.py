"""Telegram Bot API client (stdlib only). Talks to Telegram only when SEBASTIAN_ALLOW_LIVE=1; the token is read at
runtime from the secrets directory and is never logged. Tests inject fake `post`/`fetch`/`upload` callables."""
from __future__ import annotations
import json
import os
import urllib.request
import uuid

from .http import LiveAccessDenied
from .notify import split_message

MAX_DOWNLOAD = 5 * 1024 * 1024


def _guard() -> None:
    if os.environ.get("SEBASTIAN_ALLOW_LIVE") != "1":
        raise LiveAccessDenied("Live network access is disabled. Set SEBASTIAN_ALLOW_LIVE=1 only after owner approval.")


def _post(url: str, payload: dict, timeout: int) -> dict:
    _guard()
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - api.telegram.org only
        return json.load(r)


def _fetch(url: str, timeout: int) -> bytes:
    _guard()
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
        return r.read(MAX_DOWNLOAD + 1)


def multipart(fields: dict, file_field: str, filename: str, data: bytes, ctype: str) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                 f"Content-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _upload(url: str, body: bytes, content_type: str, timeout: int) -> dict:
    _guard()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return json.load(r)


class TelegramApi:
    def __init__(self, token: str, post=_post, fetch=_fetch, upload=_upload):
        if not token:
            raise ValueError("Telegram bot token is required")
        self._base = f"https://api.telegram.org/bot{token}/"
        self._file_base = f"https://api.telegram.org/file/bot{token}/"
        self._post, self._fetch, self._upload = post, fetch, upload

    def _call(self, method: str, payload: dict, timeout: int = 30):
        res = self._post(self._base + method, payload, timeout)
        if not res.get("ok"):
            raise RuntimeError(f"telegram {method} failed")
        return res["result"]

    def send(self, chat_id: int, text: str, reply_markup: dict | None = None) -> int | None:
        """Send (split if long). The inline keyboard goes on the last chunk. Returns that message's id."""
        chunks, mid = split_message(text), None
        for i, chunk in enumerate(chunks):
            payload = {"chat_id": chat_id, "text": chunk}
            if reply_markup and i == len(chunks) - 1:
                payload["reply_markup"] = reply_markup
            res = self._call("sendMessage", payload)
            mid = res.get("message_id") if isinstance(res, dict) else None
        return mid

    def answer_callback(self, callback_id: str, text: str = "", alert: bool = False) -> None:
        payload = {"callback_query_id": callback_id}
        if text:
            payload.update(text=text, show_alert=alert)
        self._call("answerCallbackQuery", payload, 10)

    def remove_keyboard(self, chat_id: int, message_id: int) -> None:
        self._call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                              "reply_markup": {"inline_keyboard": []}}, 10)

    def send_typing(self, chat_id: int) -> None:
        self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"}, 5)

    def get_file(self, file_id: str) -> bytes:
        info = self._call("getFile", {"file_id": file_id})
        data = self._fetch(self._file_base + info["file_path"], 30)
        if len(data) > MAX_DOWNLOAD:
            raise RuntimeError("file too large")
        return data

    def send_voice(self, chat_id: int, ogg: bytes) -> None:
        body, ctype = multipart({"chat_id": chat_id}, "voice", "reply.ogg", ogg, "audio/ogg")
        if not self._upload(self._base + "sendVoice", body, ctype, 60).get("ok"):
            raise RuntimeError("telegram sendVoice failed")

    def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        p = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            p["offset"] = offset
        return self._call("getUpdates", p, timeout + 10)


class FakeTelegram:
    """In-memory stand-in for tests and `sebastian chat`."""

    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.voices: list[tuple[int, bytes]] = []
        self.typing: list[int] = []
        self.inbox: list[dict] = []
        self.files: dict[str, bytes] = {}
        self.fail = False
        self.markups: list[tuple[int, dict]] = []          # (message_id, reply_markup) of messages sent with a keyboard
        self.callback_answers: list[tuple[str, str]] = []
        self.removed: list[tuple[int, int]] = []
        self._mid = 100

    def send(self, chat_id, text, reply_markup=None):
        if self.fail:
            raise RuntimeError("send failed")
        self.sent.append((chat_id, text))
        self._mid += 1
        if reply_markup:
            self.markups.append((self._mid, reply_markup))
        return self._mid

    def answer_callback(self, callback_id, text="", alert=False):
        self.callback_answers.append((callback_id, text))

    def remove_keyboard(self, chat_id, message_id):
        self.removed.append((chat_id, message_id))

    def send_typing(self, chat_id):
        self.typing.append(chat_id)

    def get_file(self, file_id):
        return self.files[file_id]

    def send_voice(self, chat_id, ogg):
        self.voices.append((chat_id, ogg))

    def get_updates(self, offset, timeout=0):
        out, self.inbox = [u for u in self.inbox if offset is None or u["update_id"] >= offset], []
        return out
