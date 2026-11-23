import json
import pytest
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs
from sebastian import oauth, config as cfg
from sebastian.http import Response, UrllibTransport, LiveAccessDenied
from sebastian.models import Account, Event
from sebastian.notify import StubNotifier, TelegramNotifier, split_message
from sebastian.providers import GoogleCalendarProvider, OutlookCalendarProvider
from sebastian.reminders import due_reminders, format_reminder
from sebastian.scheduler import Scheduler, State
from sebastian.service import Fetcher
from sebastian.cli import main

T = timezone.utc


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, headers=None, form=None):
        self.calls.append((method, url, headers, form))
        return Response(200, self.responses.pop(0))


class FixedToken:
    def access_token(self):
        return "tok"


def at(h, m=0, day=18):
    return datetime(2026, 11, day, h, m, tzinfo=T)


def ev(title, h, m, dur=30, label="personal", uid=""):
    return Event(title, at(h, m), at(h, m) + timedelta(minutes=dur), label, uid=uid)


# --- OAuth
def test_auth_request_has_pkce_and_calendar_scopes():
    r = oauth.build_auth_request("google", oauth.ClientCredentials("cid"), "http://127.0.0.1/cb")
    q = parse_qs(urlparse(r.url).query)
    assert q["code_challenge_method"] == ["S256"] and q["state"] == [r.state]
    assert q["scope"] == ["https://www.googleapis.com/auth/calendar.readonly https://www.googleapis.com/auth/calendar.events"]


def test_write_scopes_refused():
    with pytest.raises(ValueError):
        oauth.build_auth_request("google", oauth.ClientCredentials("c"), "http://x", ("https://www.googleapis.com/auth/calendar",))
    with pytest.raises(ValueError):
        oauth.build_auth_request("outlook", oauth.ClientCredentials("c"), "http://x", ("Calendars.ReadWrite",))


def test_exchange_and_refresh_and_store(tmp_path):
    ft = FakeTransport([{"access_token": "a1", "refresh_token": "r1", "expires_in": 100},
                        {"access_token": "a2", "expires_in": 100}])
    c = oauth.ClientCredentials("cid", "sec")
    tok = oauth.exchange_code("google", c, "http://x", "code", "ver", ft, now=1000)
    assert tok == oauth.Token("a1", "r1", 1100)
    assert ft.calls[0][3]["code_verifier"] == "ver"
    store = oauth.TokenStore(tmp_path / "s")
    store.save("personal", tok)
    assert oct((tmp_path / "s" / "personal.token").stat().st_mode & 0o777) == "0o600"
    now = [1090.0]
    src = oauth.TokenSource("google", "personal", c, store, ft, clock=lambda: now[0])
    assert src.access_token() == "a2"          # expired -> refreshed
    assert store.load("personal").refresh_token == "r1"  # refresh token kept


def test_token_source_without_token(tmp_path):
    src = oauth.TokenSource("google", "x", oauth.ClientCredentials("c"), oauth.TokenStore(tmp_path), FakeTransport([]))
    with pytest.raises(RuntimeError):
        src.access_token()


def test_real_transport_blocked_without_flag(monkeypatch):
    monkeypatch.delenv("SEBASTIAN_ALLOW_LIVE", raising=False)
    with pytest.raises(LiveAccessDenied):
        UrllibTransport().request("GET", "https://example.com")


# --- Providers
def test_google_pagination_and_cancelled():
    item = lambda t, st="confirmed": {"summary": t, "status": st, "start": {"dateTime": "2026-11-18T09:00:00Z"}, "end": {"dateTime": "2026-11-18T10:00:00Z"}}
    ft = FakeTransport([{"items": [item("A"), item("B", "cancelled")], "nextPageToken": "p2"}, {"items": [item("C")]}])
    p = GoogleCalendarProvider(Account("google", "a@b.c", "personal"), FixedToken(), ft)
    out = p.events(at(0), at(0, day=20))
    assert [e.title for e in out] == ["A", "C"]
    assert "pageToken=p2" in ft.calls[1][1] and ft.calls[0][2]["Authorization"] == "Bearer tok"
    assert all(c[0] == "GET" for c in ft.calls)


def test_outlook_pagination_and_all_day():
    mk = lambda s, **k: {"subject": s, "start": {"dateTime": "2026-11-18T09:00:00.0000000", "timeZone": "UTC"},
                         "end": {"dateTime": "2026-11-18T10:00:00.0000000", "timeZone": "UTC"}, **k}
    ft = FakeTransport([{"value": [mk("A"), mk("X", isCancelled=True)], "@odata.nextLink": "https://graph.microsoft.com/next"},
                        {"value": [mk("B", isAllDay=True)]}])
    p = OutlookCalendarProvider(Account("outlook", "a@c.d", "company"), FixedToken(), ft)
    out = p.events(at(0), at(0, day=20))
    assert [e.title for e in out] == ["A", "B"] and out[1].all_day
    assert ft.calls[1][1] == "https://graph.microsoft.com/next"
    assert ft.calls[0][2]["Prefer"] == 'outlook.timezone="UTC"'


def test_fetcher_isolates_failures():
    class Bad:
        account = Account("outlook", "x", "company")
        def events(self, s, e): raise RuntimeError("boom")
    class Good:
        account = Account("google", "x", "personal")
        def events(self, s, e): return [ev("A", 9, 0)]
    f = Fetcher([Bad(), Good()])
    assert len(f(at(8))) == 1 and f.errors == ["company: RuntimeError"]


# --- Notify
def test_split_message():
    parts = split_message("\n".join(["x" * 100] * 100), limit=1000)
    assert all(len(p) <= 1000 for p in parts) and len(parts) > 1


def test_telegram_notifier_posts_to_fixed_chat():
    ft = FakeTransport([{"ok": True}])
    TelegramNotifier("BOT", "42", ft).send("hi")
    m, url, _, form = ft.calls[0]
    assert m == "POST" and url.endswith("botBOT/sendMessage") and form == {"chat_id": "42", "text": "hi"}


# --- Reminders
def test_reminders_window_and_dedupe():
    evs = [ev("Soon", 9, 10), ev("Later", 11, 0)]
    due = due_reminders(evs, at(9, 0), timedelta(minutes=15), set())
    assert [e.title for e in due] == ["Soon"]
    assert "10 dk sonra" in format_reminder(due[0], at(9, 0))
    assert due_reminders(evs, at(9, 0), timedelta(minutes=15), {"soon|" + at(9, 10).isoformat()}) == []


# --- Scheduler
def make_sched(events, tmp_path):
    n = StubNotifier()
    s = Scheduler(cfg.parse({}).morning, cfg.parse({}).evening, lambda now: events, n, State(tmp_path / "st.json"), timedelta(minutes=15))
    return s, n


def test_digest_fires_once_per_day(tmp_path):
    s, n = make_sched([ev("A", 9, 0)], tmp_path)
    assert s.tick(at(8, 29)) == []
    assert len(s.tick(at(8, 30))) == 1
    s.tick(at(8, 31))
    assert sum("Günaydın" in m for m in n.sent) == 1
    # restart: new scheduler, same state file -> no resend
    s2, n2 = make_sched([ev("A", 9, 0)], tmp_path)
    s2.tick(at(8, 40))
    assert not any("Günaydın" in m for m in n2.sent)


def test_missed_digest_beyond_catchup_is_skipped(tmp_path):
    s, n = make_sched([], tmp_path)
    assert s.tick(at(12, 0)) == []


def test_evening_and_reminder_once(tmp_path):
    s, n = make_sched([ev("A", 20, 10)], tmp_path)
    out = s.tick(at(20, 0))
    assert any(m.startswith("İyi akşamlar") for m in out) and any(m.startswith("Hatırlatma") for m in out)
    assert s.tick(at(20, 1)) == []


def test_run_forever_bounded(tmp_path):
    s, n = make_sched([], tmp_path)
    ticks = iter([at(8, 30), at(8, 31)])
    s.run_forever(lambda: next(ticks), sleep=lambda x: None, max_ticks=2)
    assert len(n.sent) == 1


# --- Config / CLI
def test_config_validation():
    with pytest.raises(cfg.ConfigError):
        cfg.parse({"timezone": "Nope/Zone"})
    with pytest.raises(cfg.ConfigError):
        cfg.parse({"morning": "8h30"})
    with pytest.raises(cfg.ConfigError):
        cfg.parse({"accounts": [{"provider": "yahoo", "address": "a", "label": "x"}]})
    c = cfg.parse({"accounts": [{"provider": "google", "address": "a", "label": "x"}]})
    assert c.accounts[0].enabled is False and c.reminder_lead_minutes == 30


def test_cli_demo_and_live_guard(tmp_path, capsys, monkeypatch):
    p = tmp_path / "c.toml"
    p.write_text('timezone = "UTC"\n[[accounts]]\nprovider="google"\naddress="a@b.c"\nlabel="personal"\nenabled=true\n')
    main(["--config", str(p), "demo", "morning"])
    assert "Günaydın" in capsys.readouterr().out
    monkeypatch.delenv("SEBASTIAN_ALLOW_LIVE", raising=False)
    with pytest.raises(SystemExit) as e:
        main(["--config", str(p), "digest", "morning"])
    assert "disabled" in str(e.value)


def test_example_config_parses():
    import tomllib, pathlib
    root = pathlib.Path(__file__).parent.parent
    c = cfg.parse(tomllib.loads((root / "config.example.toml").read_text()))
    assert len(c.accounts) == 2 and not any(a.enabled for a in c.accounts)
