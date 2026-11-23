"""Console channel: type to the assistant in a terminal. For development, tests and fork users without a messaging account.

Buttons are rendered as numbered options; typing the number taps that button."""
from __future__ import annotations
import time
from typing import Callable


class ConsoleChannel:
    def __init__(self, owner: int = 1, read: Callable[[], str | None] | None = None, write: Callable[[str], None] = print):
        self.owner, self._read, self._write = owner, read, write
        self._uid, self._mid = 0, 0
        self._buttons: list[str] = []                       # callback_data of the last keyboard shown, 1-based when typed
        self.pending: list[str] = []                        # lines fed programmatically (tests)

    def feed(self, text: str) -> None:
        self.pending.append(text)

    def send(self, chat_id: int, text: str, reply_markup: dict | None = None) -> int | None:
        lines = [text]
        self._buttons = []
        for row in (reply_markup or {}).get("inline_keyboard", []):
            for b in row:
                self._buttons.append(b["callback_data"])
                lines.append(f"  {len(self._buttons)}) {b['text']}")
        self._write("\n".join(lines))
        self._mid += 1
        return self._mid

    def get_updates(self, offset: int | None, timeout: int = 0) -> list[dict]:
        text = self.pending.pop(0) if self.pending else (self._read() if self._read else None)
        if text is None or not text.strip():
            return []
        self._uid += 1
        chat = {"id": self.owner, "type": "private"}
        t = text.strip()
        if t.isdigit() and 1 <= int(t) <= len(self._buttons):
            return [{"update_id": self._uid, "callback_query": {"id": f"c{self._uid}", "from": {"id": self.owner}, "data": self._buttons[int(t) - 1],
                                                                "message": {"message_id": self._mid, "chat": chat}}}]
        return [{"update_id": self._uid, "message": {"message_id": self._uid, "chat": chat, "from": {"id": self.owner}, "date": int(time.time()), "text": t}}]

    def answer_callback(self, callback_id: str, text: str = "", alert: bool = False) -> None:
        if text:
            self._write(f"({text})")

    def remove_keyboard(self, chat_id: int, message_id: int) -> None:
        self._buttons = []

    def send_typing(self, chat_id: int) -> None:
        pass

    def get_file(self, file_id: str) -> bytes:
        raise RuntimeError("the console has no attachments")

    def send_voice(self, chat_id: int, ogg: bytes) -> None:
        raise RuntimeError("the console cannot play voice notes")
