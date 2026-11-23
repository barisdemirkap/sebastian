"""The channel contract (ADR 0002): everything the bot needs from a messaging platform.

The assistant core and the bot never import a platform SDK; they talk to a `Channel`. Inbound traffic is normalized to the
update shape below (the Telegram shape, kept because the bot, the retry queue and the conformance suite already speak it), so a new
platform only translates its own events into it:

    {"update_id": int,                                   # strictly increasing per channel
     "message": {"message_id": int, "chat": {"id": int, "type": "private"}, "from": {"id": int},
                 "date": int, "text": str}}              # or "voice": {"file_id": str, ...}
    {"update_id": int, "callback_query": {"id": str, "from": {"id": int}, "data": str,
                 "message": {"message_id": int, "chat": {"id": int, "type": "private"}}}}

Outbound keyboards use the inline-keyboard shape ({"inline_keyboard": [[{"text", "callback_data"}]]}); a channel without buttons
renders them as numbered text and maps a typed number back to the callback data (the preview + confirm gate works everywhere)."""
from __future__ import annotations
from typing import Protocol, runtime_checkable


@runtime_checkable
class Channel(Protocol):
    def send(self, chat_id: int, text: str, reply_markup: dict | None = None) -> int | None:
        """Deliver a message (split if the platform needs it); returns the id of the last message sent."""

    def get_updates(self, offset: int | None, timeout: int = 0) -> list[dict]:
        """Normalized inbound updates with update_id >= offset."""

    def answer_callback(self, callback_id: str, text: str = "", alert: bool = False) -> None:
        """Acknowledge a button tap (a no-op where the platform has no such concept)."""

    def remove_keyboard(self, chat_id: int, message_id: int) -> None:
        """Drop the buttons of an earlier message."""

    def send_typing(self, chat_id: int) -> None:
        """Show a typing indicator (may be a no-op)."""

    def get_file(self, file_id: str) -> bytes:
        """Download an attachment (voice notes)."""

    def send_voice(self, chat_id: int, ogg: bytes) -> None:
        """Send a voice note (may raise where unsupported; callers fall back to text)."""
