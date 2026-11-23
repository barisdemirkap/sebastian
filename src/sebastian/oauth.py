"""OAuth 2.0 authorization-code + PKCE flow for Google and Microsoft (read scopes, plus calendar.events on Google).

Code only: nothing here runs against a live provider until the owner approves consent.
Token files live outside the repo with mode 0600."""
from __future__ import annotations
import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlencode
from .http import Transport

SECRETS_DIR = Path.home() / ".config" / "sebastian" / "secrets"
READONLY_SCOPES = {
    "google": ("https://www.googleapis.com/auth/calendar.readonly",),
    "outlook": ("Calendars.Read", "offline_access"),
}


@dataclass(frozen=True)
class OAuthEndpoints:
    auth: str
    token: str
    extra_auth_params: tuple[tuple[str, str], ...] = ()


# ADR 0002: the owner allowed preview-gated calendar writes on Google. Narrowest write scope: events only.
WRITE_SCOPES = {
    "google": ("https://www.googleapis.com/auth/calendar.events",),
    "outlook": (),
}
DEFAULT_SCOPES = {
    "google": READONLY_SCOPES["google"] + WRITE_SCOPES["google"],
    "outlook": READONLY_SCOPES["outlook"],
}

# Mail scopes are never requested by default: the owner must pass them explicitly in config (Phase 2 gate).
MAIL_READONLY_SCOPES = {
    "google": ("https://www.googleapis.com/auth/gmail.readonly",),
    "outlook": ("Mail.Read",),
}

ENDPOINTS = {
    "google": OAuthEndpoints("https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token",
                             (("access_type", "offline"), ("prompt", "consent"))),
    "outlook": OAuthEndpoints("https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                              "https://login.microsoftonline.com/common/oauth2/v2.0/token"),
}


@dataclass(frozen=True)
class ClientCredentials:
    client_id: str
    client_secret: str = ""  # empty for public clients (PKCE only)


@dataclass(frozen=True)
class Token:
    access_token: str
    refresh_token: str
    expires_at: float

    def expired(self, now: float, skew: float = 60) -> bool:
        return now >= self.expires_at - skew


@dataclass(frozen=True)
class AuthRequest:
    url: str
    state: str
    verifier: str


def check_scopes(provider: str, scopes: tuple[str, ...]) -> None:
    """Reject anything that is not an allowed scope: read scopes, mail read scopes, and the one write scope of ADR 0002."""
    ok = READONLY_SCOPES[provider] + MAIL_READONLY_SCOPES[provider] + WRITE_SCOPES[provider] + ("openid", "email", "profile")
    bad = [s for s in scopes if s not in ok]
    if bad:
        raise ValueError(f"Scopes refused (not allowed by ADR 0001/0002): {bad}")


def build_auth_request(provider: str, creds: ClientCredentials, redirect_uri: str,
                       scopes: tuple[str, ...] | None = None) -> AuthRequest:
    scopes = scopes or DEFAULT_SCOPES[provider]
    check_scopes(provider, scopes)
    ep = ENDPOINTS[provider]
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    params = {"client_id": creds.client_id, "redirect_uri": redirect_uri, "response_type": "code",
              "scope": " ".join(scopes), "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
              **dict(ep.extra_auth_params)}
    return AuthRequest(f"{ep.auth}?{urlencode(params)}", state, verifier)


def _token_from(body: dict, now: float, old_refresh: str = "") -> Token:
    return Token(body["access_token"], body.get("refresh_token", old_refresh), now + int(body.get("expires_in", 3600)))


def exchange_code(provider: str, creds: ClientCredentials, redirect_uri: str, code: str, verifier: str,
                  transport: Transport, now: float | None = None) -> Token:
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": creds.client_id, "code_verifier": verifier}
    if creds.client_secret:
        form["client_secret"] = creds.client_secret
    r = transport.request("POST", ENDPOINTS[provider].token, form=form)
    return _token_from(r.body, now if now is not None else time.time())


def refresh(provider: str, creds: ClientCredentials, token: Token, transport: Transport,
            now: float | None = None) -> Token:
    form = {"grant_type": "refresh_token", "refresh_token": token.refresh_token, "client_id": creds.client_id}
    if creds.client_secret:
        form["client_secret"] = creds.client_secret
    r = transport.request("POST", ENDPOINTS[provider].token, form=form)
    return _token_from(r.body, now if now is not None else time.time(), token.refresh_token)


class TokenStore:
    def __init__(self, directory: Path = SECRETS_DIR):
        self.dir = Path(directory)

    def _path(self, label: str) -> Path:
        if not label.replace("-", "").replace("_", "").isalnum():
            raise ValueError("invalid account label")
        return self.dir / f"{label}.token"

    def save(self, label: str, token: Token) -> None:
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        p = self._path(label)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(token), f)
        os.chmod(p, 0o600)

    def load(self, label: str) -> Token | None:
        p = self._path(label)
        return Token(**json.loads(p.read_text())) if p.exists() else None


class TokenSource:
    """Returns a valid access token, refreshing and persisting when needed."""

    def __init__(self, provider: str, label: str, creds: ClientCredentials, store: TokenStore,
                 transport: Transport, clock=time.time):
        self.provider, self.label, self.creds, self.store, self.transport, self.clock = provider, label, creds, store, transport, clock

    def access_token(self) -> str:
        tok = self.store.load(self.label)
        if tok is None:
            raise RuntimeError(f"Account '{self.label}' is not connected (no token). Run the auth flow after owner approval.")
        if tok.expired(self.clock()):
            tok = refresh(self.provider, self.creds, tok, self.transport, self.clock())
            self.store.save(self.label, tok)
        return tok.access_token
