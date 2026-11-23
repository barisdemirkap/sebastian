"""Wires config -> providers -> digests -> notifier. Live wiring needs owner-approved tokens."""
from __future__ import annotations
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from .config import Config, CONFIG_DIR
from .http import Transport, UrllibTransport
from .mail import GmailProvider, OutlookMailProvider, MailMessage
from .models import Account, Event
from .notify import Notifier, StubNotifier, TelegramNotifier
from .oauth import ClientCredentials, TokenSource, TokenStore
from .providers import GoogleCalendarProvider, OutlookCalendarProvider, FixtureProvider
from .scheduler import Scheduler, State


def build_provider(acct: Account, cfg: Config, store: TokenStore, transport: Transport):
    cls = {"google": GoogleCalendarProvider, "outlook": OutlookCalendarProvider}[acct.provider]
    secret_file = store.dir / f"{acct.provider}.client_secret"
    secret = secret_file.read_text().strip() if secret_file.exists() else ""
    creds = ClientCredentials(cfg.oauth_client_ids.get(acct.provider, ""), secret)
    kw = {"tz": ZoneInfo(cfg.timezone), "birthdays": cfg.birthdays} if acct.provider == "google" else {}
    return cls(acct, TokenSource(acct.provider, acct.label, creds, store, transport), transport, **kw)


class Fetcher:
    """Fetches events from all enabled accounts; one failing account never blocks the rest."""

    def __init__(self, providers: list, cache_seconds: int = 300, days: int = 5):
        self.providers, self.cache_seconds, self.days = providers, cache_seconds, days
        self.errors: list[str] = []
        self._cache: tuple[datetime, list[Event]] | None = None

    def __call__(self, now: datetime) -> list[Event]:
        if self._cache and (now - self._cache[0]).total_seconds() < self.cache_seconds:
            return self._cache[1]
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=self.days)
        out: list[Event] = []
        self.errors = []
        for p in self.providers:
            try:
                out += p.events(start, end)
            except Exception as e:  # noqa: BLE001 - report, keep going
                self.errors.append(f"{getattr(p, 'account', None) and p.account.label}: {type(e).__name__}")
        self._cache = (now, out)
        return out


    def invalidate(self) -> None:
        self._cache = None


class CalendarHub:
    """What the assistant talks to: reads merge every provider (failures isolated), writes go to the one writable
    provider (first with create_event). Invalidates the fetch cache after each write."""

    def __init__(self, providers: list, fetcher: "Fetcher | None" = None):
        self.providers, self.fetcher, self.errors = providers, fetcher, []

    def events(self, start: datetime, end: datetime) -> list[Event]:
        out, self.errors = [], []
        for p in self.providers:
            try:
                out += p.events(start, end)
            except Exception as e:  # noqa: BLE001
                self.errors.append(f"{getattr(p, 'account', None) and p.account.label}: {type(e).__name__}")
        return out

    @property
    def writer(self):
        return next((p for p in self.providers if hasattr(p, "create_event")), None)

    def _w(self):
        if self.writer is None:
            raise RuntimeError("no writable calendar")
        return self.writer

    def _done(self) -> None:
        if self.fetcher:
            self.fetcher.invalidate()

    def create_event(self, draft):
        r = self._w().create_event(draft); self._done(); return r

    def update_event(self, event_id, changes):
        r = self._w().update_event(event_id, changes); self._done(); return r

    def delete_event(self, event_id):
        self._w().delete_event(event_id); self._done()


def build_mail_provider(acct: Account, cfg: Config, store: TokenStore, transport: Transport):
    cls = {"google": GmailProvider, "outlook": OutlookMailProvider}[acct.provider]
    secret_file = store.dir / f"{acct.provider}.client_secret"
    secret = secret_file.read_text().strip() if secret_file.exists() else ""
    creds = ClientCredentials(cfg.oauth_client_ids.get(acct.provider, ""), secret)
    return cls(acct, TokenSource(acct.provider, acct.label, creds, store, transport), transport)


class MailFetcher:
    """Fetches the last `days` of mail metadata from every mail-enabled account; failures are isolated."""

    def __init__(self, providers: list, days: int = 7, cache_seconds: int = 300):
        self.providers, self.days, self.cache_seconds = providers, days, cache_seconds
        self.errors: list[str] = []
        self._cache: tuple[datetime, list[MailMessage]] | None = None

    def __call__(self, now: datetime) -> list[MailMessage]:
        if self._cache and (now - self._cache[0]).total_seconds() < self.cache_seconds:
            return self._cache[1]
        out: list[MailMessage] = []
        self.errors = []
        for p in self.providers:
            try:
                out += p.messages(now - timedelta(days=self.days), now + timedelta(minutes=1))
            except Exception as e:  # noqa: BLE001
                self.errors.append(f"{getattr(p, 'account', None) and p.account.label}: {type(e).__name__}")
        self._cache = (now, out)  # short-lived, in memory only
        return out


def build_mail_fetcher(cfg: Config, store: TokenStore | None = None, transport: Transport | None = None) -> MailFetcher | None:
    accts = [a for a in cfg.accounts if a.enabled and a.mail_enabled]
    if not accts:
        return None
    store, transport = store or TokenStore(), transport or UrllibTransport()
    return MailFetcher([build_mail_provider(a, cfg, store, transport) for a in accts])


def build_notifier(cfg: Config, store: TokenStore, transport: Transport, dry_run: bool) -> Notifier:
    if dry_run or not cfg.telegram.enabled:
        return StubNotifier(CONFIG_DIR / "outbox.jsonl") if not dry_run else StubNotifier()
    token = (store.dir / (cfg.telegram.token_file or "telegram.token")).read_text().strip()
    return TelegramNotifier(token, cfg.telegram.chat_id, transport)


def build_scheduler(cfg: Config, fetch, notifier: Notifier, state_path: Path | None = None, mail_fetch=None, tasks=None, lists=None, nag=None) -> Scheduler:
    return Scheduler(cfg.morning, cfg.evening, fetch, notifier, State(state_path or cfg.state_path),
                     timedelta(minutes=cfg.reminder_lead_minutes), mail_fetch, tuple(a.address for a in cfg.accounts),
                     cfg.plan, cfg.allday_reminder, tasks, lists, nag)


def build_live(cfg: Config, dry_run: bool = True, store: TokenStore | None = None, transport: Transport | None = None):
    store = store or TokenStore()
    transport = transport or UrllibTransport()
    providers = [build_provider(a, cfg, store, transport) for a in cfg.accounts if a.enabled]
    return Fetcher(providers), build_notifier(cfg, store, transport, dry_run)


__all__ = ["build_provider", "build_mail_fetcher", "MailFetcher", "Fetcher", "build_notifier", "build_scheduler", "build_live", "FixtureProvider"]
