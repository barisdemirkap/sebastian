"""Minimal HTTP seam. All network access goes through a Transport so tests use fakes.

The real transport refuses to run unless SEBASTIAN_ALLOW_LIVE=1 (approval gate)."""
from __future__ import annotations
import json
import os
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Response:
    status: int
    body: dict


class HttpError(Exception):
    def __init__(self, status: int, body: dict):
        super().__init__(f"HTTP {status}")
        self.status, self.body = status, body


class LiveAccessDenied(RuntimeError):
    pass


class WriteAccessDenied(RuntimeError):
    pass


CALENDAR_API = "https://www.googleapis.com/calendar/"


class Transport(Protocol):
    def request(self, method: str, url: str, headers: dict[str, str] | None = None,
                form: dict[str, str] | None = None, json_body: dict | None = None) -> Response: ...


class UrllibTransport:
    """Real network transport. Not exercised by tests."""

    def request(self, method, url, headers=None, form=None, json_body=None):
        if os.environ.get("SEBASTIAN_ALLOW_LIVE") != "1":
            raise LiveAccessDenied("Live network access is disabled. Set SEBASTIAN_ALLOW_LIVE=1 only after owner approval.")
        if method != "GET" and url.startswith(CALENDAR_API) and os.environ.get("SEBASTIAN_ALLOW_WRITE") != "1":
            raise WriteAccessDenied("Calendar writes are disabled. Set SEBASTIAN_ALLOW_WRITE=1 (owner decision, ADR 0002).")
        headers = dict(headers or {})
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        if json_body is not None:
            data = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return Response(r.status, json.loads(r.read() or b"{}"))
        except urllib.error.HTTPError as e:
            raise HttpError(e.code, json.loads(e.read() or b"{}")) from e
