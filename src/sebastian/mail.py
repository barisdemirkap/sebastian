"""Read-only mail readers (Gmail, Microsoft Graph) behind the `MailProvider` interface.

Phase 2. Only GET requests, metadata only: message bodies are never requested, stored, or logged.
Nothing here runs live until the owner approves mail scopes (SEBASTIAN_ALLOW_LIVE=1, `enabled` and `mail_enabled`)."""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import getaddresses, parsedate_to_datetime
from typing import Protocol
from urllib.parse import urlencode, quote
from .http import Transport
from .models import Account


@dataclass(frozen=True)
class MailMessage:
    """Metadata only. No body, no snippet."""
    account_label: str
    thread_id: str
    message_id: str
    sender: str            # lowercase address
    recipients: tuple[str, ...]
    subject: str
    date: datetime
    from_owner: bool = False
    sender_name: str = ""


class MailProvider(Protocol):
    def messages(self, since: datetime, until: datetime) -> list[MailMessage]: ...


class _Token(Protocol):
    def access_token(self) -> str: ...


def _addrs(value: str) -> tuple[str, ...]:
    return tuple(a.lower() for _, a in getaddresses([value]) if a)


def parse_gmail_message(item: dict, account: Account) -> MailMessage:
    h = {x["name"].lower(): x["value"] for x in item.get("payload", {}).get("headers", [])}
    name, sender = (getaddresses([h.get("from", "")]) or [("", "")])[0]
    sender = sender.lower()
    try:
        date = parsedate_to_datetime(h["date"])
    except (KeyError, TypeError, ValueError):
        date = datetime.fromtimestamp(int(item.get("internalDate", "0")) / 1000, tz=timezone.utc)
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return MailMessage(account.label, item["threadId"], item["id"], sender,
                       _addrs(h.get("to", "")) + _addrs(h.get("cc", "")), h.get("subject", "(no subject)"), date,
                       sender == account.address.lower() or "SENT" in item.get("labelIds", []), name)


def parse_graph_message(item: dict, account: Account) -> MailMessage:
    frm = (item.get("from") or {}).get("emailAddress", {})
    sender = frm.get("address", "").lower()
    rcpts = tuple(r["emailAddress"]["address"].lower() for k in ("toRecipients", "ccRecipients")
                  for r in item.get(k, []))
    date = datetime.fromisoformat(item["receivedDateTime"].replace("Z", "+00:00"))
    return MailMessage(account.label, item["conversationId"], item["id"], sender, rcpts,
                       item.get("subject") or "(no subject)", date, sender == account.address.lower(), frm.get("name", ""))


def _epoch(d: datetime) -> int:
    return int(d.timestamp())


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GmailProvider:
    """Read-only Gmail (scope gmail.readonly): list ids, then fetch metadata headers only."""
    BASE = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
    HEADERS = ("From", "To", "Cc", "Subject", "Date")

    def __init__(self, account: Account, tokens: _Token, transport: Transport, max_pages: int = 5, max_messages: int = 300):
        self.account, self.tokens, self.transport = account, tokens, transport
        self.max_pages, self.max_messages = max_pages, max_messages

    def _get(self, url: str) -> dict:
        return self.transport.request("GET", url, {"Authorization": f"Bearer {self.tokens.access_token()}"}).body

    def messages(self, since: datetime, until: datetime) -> list[MailMessage]:
        ids: list[str] = []
        page = ""
        for _ in range(self.max_pages):
            q = {"q": f"after:{_epoch(since)} before:{_epoch(until)}", "maxResults": "100"}
            if page:
                q["pageToken"] = page
            body = self._get(f"{self.BASE}?{urlencode(q)}")
            ids += [m["id"] for m in body.get("messages", [])]
            page = body.get("nextPageToken", "")
            if not page or len(ids) >= self.max_messages:
                break
        out = []
        for mid in ids[: self.max_messages]:
            meta = urlencode([("format", "metadata")] + [("metadataHeaders", h) for h in self.HEADERS])
            out.append(parse_gmail_message(self._get(f"{self.BASE}/{quote(mid, safe='')}?{meta}"), self.account))
        return out


class OutlookMailProvider:
    """Read-only Microsoft Graph mail (scope Mail.Read). `$select` excludes body fields."""
    BASE = "https://graph.microsoft.com/v1.0/me/messages"
    SELECT = "id,conversationId,subject,from,toRecipients,ccRecipients,receivedDateTime"

    def __init__(self, account: Account, tokens: _Token, transport: Transport, max_pages: int = 5):
        self.account, self.tokens, self.transport, self.max_pages = account, tokens, transport, max_pages

    def messages(self, since: datetime, until: datetime) -> list[MailMessage]:
        q = {"$select": self.SELECT, "$top": "100", "$orderby": "receivedDateTime desc",
             "$filter": f"receivedDateTime ge {_iso(since)} and receivedDateTime lt {_iso(until)}"}
        url = f"{self.BASE}?{urlencode(q)}"
        out: list[MailMessage] = []
        for _ in range(self.max_pages):
            r = self.transport.request("GET", url, {"Authorization": f"Bearer {self.tokens.access_token()}"})
            out += [parse_graph_message(i, self.account) for i in r.body.get("value", []) if not i.get("isDraft")]
            url = r.body.get("@odata.nextLink", "")
            if not url:
                break
        return out


class FixtureMailProvider:
    def __init__(self, messages: list[MailMessage]):
        self._m = messages

    def messages(self, since: datetime, until: datetime) -> list[MailMessage]:
        return [m for m in self._m if since <= m.date < until]


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def redact(text: str) -> str:
    """Mask email addresses for logs and error reports."""
    return _EMAIL.sub(lambda m: m.group(0)[0] + "***@" + m.group(0).split("@")[1], text)
