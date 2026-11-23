from __future__ import annotations
import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from . import i18n, config as cfg
from .digest import morning_digest, evening_digest
from .oauth import ClientCredentials, TokenStore, build_auth_request, exchange_code, ENDPOINTS
from .samples import sample_events, sample_mail
from .service import build_live, build_scheduler, build_mail_fetcher

REDIRECT = "http://127.0.0.1:8765/callback"


def _load(a) -> cfg.Config:
    c = cfg.load(Path(a.config)) if a.config else cfg.load()
    i18n.configure(c.owner_title, c.assistant_name)        # [persona] in the config file
    return c


def _now(c: cfg.Config) -> datetime:
    return datetime.now(ZoneInfo(c.timezone))


def _digest(kind: str, events, now, messages=None, owners=()) -> str:
    return (morning_digest if kind == "morning" else evening_digest)(events, now, messages, owners)


def _need_live(c: cfg.Config) -> None:
    if not any(x.enabled for x in c.accounts):
        raise SystemExit("No enabled accounts. Nothing is connected until OAuth consent is given.")
    if os.environ.get("SEBASTIAN_ALLOW_LIVE") != "1":
        raise SystemExit("Live access is disabled. Set SEBASTIAN_ALLOW_LIVE=1 only after owner approval.")


def cmd_accounts(a, c):
    for x in c.accounts:
        print(f"{x.label}: {x.provider} {x.address} enabled={x.enabled}")


def cmd_demo(a, c):
    now = _now(c)
    print(_digest(a.kind, sample_events(now), now, sample_mail(now), ("me@example.com",)))


def cmd_digest(a, c):
    _need_live(c)
    fetch, notifier = build_live(c, dry_run=not a.send)
    now = _now(c)
    mail = build_mail_fetcher(c)
    text = _digest(a.kind, fetch(now), now, mail(now) if mail else None, tuple(x.address for x in c.accounts))
    print(text)
    if a.send:
        notifier.send(text)


def cmd_run(a, c):
    _need_live(c)
    fetch, notifier = build_live(c, dry_run=a.dry_run)
    tasks = lists = None
    if not a.dry_run:                  # real deliveries are logged (ADR 0010) and the digests list the to-dos (ADR 0013)
        from .bot import ConfigurationError, owner_from_env
        from .notify import LoggingNotifier
        from .store import Store
        try:
            owner, store = owner_from_env(), Store(c.db_path)
        except ConfigurationError as exc:
            raise SystemExit(str(exc))
        notifier = LoggingNotifier(notifier, store, owner, lambda: _now(c))
        tasks = lambda: [r["title"] for r in store.open_tasks(owner)]  # noqa: E731
        from . import lists as listmod
        lists = lambda: listmod.digest_rows(store, owner)  # noqa: E731
    sched = build_scheduler(c, fetch, notifier, mail_fetch=build_mail_fetcher(c), tasks=tasks, lists=lists)
    sched.run_forever(lambda: _now(c), interval=a.interval, max_ticks=a.max_ticks)


def _secrets_dir() -> Path:
    from .oauth import SECRETS_DIR
    return SECRETS_DIR


def cmd_bot(a, c):
    """Long-running bot on the configured channel (Telegram by default; owner only). Needs SEBASTIAN_ALLOW_LIVE=1, SEBASTIAN_OWNER_CHAT_ID, the bot token file."""
    from . import voice as voice_mod
    from .bot import build_bot, ConfigurationError
    from .service import CalendarHub
    from .bot import owner_from_env
    from .channels.factory import make_channel
    _need_live(c)
    channel = make_channel(c, _secrets_dir(), owner_from_env(os.environ))
    fetch, _ = build_live(c, dry_run=True)
    try:
        bot = build_bot(c, os.environ, channel, CalendarHub(fetch.providers, fetch), fetch,
                        build_mail_fetcher(c))
    except ConfigurationError as exc:
        raise SystemExit(str(exc))
    if bot.voice is not None and (missing := bot.voice.missing()):
        print(f"voice enabled but missing: {', '.join(missing)}")
    print("sebastian bot running; Ctrl+C to stop")
    try:
        bot.run()
    except KeyboardInterrupt:
        pass


def cmd_chat(a, c):
    """Talk to the assistant in the terminal with a fixture calendar and an in-memory store: no network, no Telegram."""
    from zoneinfo import ZoneInfo
    from . import nl as nlmod
    from .assistant import Assistant
    from .providers import FixtureProvider
    from .store import Store
    tz = ZoneInfo(c.timezone)
    hub = FixtureProvider(sample_events(_now(c)))
    asst = Assistant(hub, Store(), nlmod.NLService.from_env(), tz, 1, default_lead=c.reminder_lead_minutes)
    print("Sebastian demo chat (fixture calendar, nothing is saved). Ctrl+D to quit.")
    for line in _lines():
        for r in asst.handle(1, line):
            print(r)


def _lines():
    while True:
        try:
            yield input("> ")
        except EOFError:
            return


def cmd_import_plan(a, c):
    """Turn a plan file into ONE pending bulk add for the owner: preview here, confirm with 'evet' in Telegram."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from . import i18n, nl as nlmod
    from .assistant import Assistant
    from .bot import ConfigurationError, owner_from_env
    from .importer import load_plan
    from .providers import FixtureProvider
    from .store import Store
    tz = ZoneInfo(c.timezone)
    now = _now(c)
    try:
        owner = owner_from_env()
        plan = load_plan(a.file, tz, now.date())
    except (ConfigurationError, ValueError) as exc:
        raise SystemExit(str(exc))
    for p in plan.problems:
        print(f"skipped: {p}")
    if not plan.drafts:
        raise SystemExit("Nothing to import.")
    asst = Assistant(FixtureProvider([]), Store(c.db_path), nlmod.NLService("off"), tz, owner, lambda: now)
    preview = "\n".join(asst.propose_import(plan.drafts, now, timedelta(minutes=a.ttl_minutes)))
    text = i18n.t("import_preview_note", n=len(plan.drafts)) + "\n" + preview
    print(text)
    if a.send:
        _push_preview(c, asst, owner, now, text)
        print("Preview sent to the owner chat. Reply 'evet' there (the bot must be running).")
    else:
        print(f"Stored as a pending action for {a.ttl_minutes} minutes. Reply 'evet' to the bot in Telegram to add (use --send to push this preview).")


def _push_preview(c: cfg.Config, asst, owner: int, now: datetime, text: str) -> None:
    """Send an operator-made preview to the owner chat (live) with its buttons, and log it so the bot can read it back."""
    from .telegram import TelegramApi
    _need_live(c)
    api = TelegramApi((_secrets_dir() / (c.telegram.token_file or "telegram.token")).read_text().strip())
    mid = api.send(owner, text, reply_markup=asst.keyboard())
    if mid is not None:
        asst.store.set_kb(owner, mid, asst.shown)
    asst.store.log(owner, text, "out", now)  # the bot must be able to read what the CLI sent (conversation context)


def cmd_list_add(a, c):
    """Seed a named list for the owner: ONE pending add preview (same confirm gate as everything else), optionally sent
    to the owner chat with a 24 h limit. The items only reach the list after the owner's 'evet' or Kaydet tap."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from . import i18n, lists as listmod, nl as nlmod
    from .assistant import Assistant
    from .bot import ConfigurationError, owner_from_env
    from .providers import FixtureProvider
    from .store import Store
    from .textutil import fold
    key = next((k for k, v in i18n.LISTS.items() if fold(a.list) == k or fold(a.list) in v["aliases"]), None)
    if key is None:
        raise SystemExit(f"Unknown list {a.list!r}. Known lists: {', '.join(i18n.LISTS)}.")
    items = listmod.split_items(", ".join(a.items))
    if not items:
        raise SystemExit("Nothing to add.")
    try:
        owner = owner_from_env()
    except ConfigurationError as exc:
        raise SystemExit(str(exc))
    now = _now(c)
    asst = Assistant(FixtureProvider([]), Store(c.db_path), nlmod.NLService("off"), ZoneInfo(c.timezone), owner, lambda: now)
    text = "\n".join(asst.propose_list_add(key, items, now, timedelta(minutes=a.ttl_minutes)))
    print(text)
    if a.send:
        _push_preview(c, asst, owner, now, text)
        print("Preview sent to the owner chat. Reply 'evet' there (the bot must be running).")
    else:
        print(f"Stored as a pending action for {a.ttl_minutes} minutes (use --send to push this preview).")


def parse_redirect(value: str, state: str | None) -> tuple[str, str | None]:
    """auth-finish accepts the code, or the whole redirected URL copied from the browser (easier on a phone)."""
    from urllib.parse import urlparse, parse_qs
    if value.startswith("http"):
        q = parse_qs(urlparse(value).query)
        return q.get("code", [""])[0], q.get("state", [state or ""])[0]
    return value, state


def cmd_auth_url(a, c):
    acct = next((x for x in c.accounts if x.label == a.label), None)
    if not acct:
        raise SystemExit(f"Unknown account label {a.label!r}")
    req = build_auth_request(acct.provider, ClientCredentials(c.oauth_client_ids.get(acct.provider, "")), REDIRECT,
                             acct.scopes or None)
    store = TokenStore()
    store.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    p = store.dir / f"{a.label}.pending"
    p.write_text(json.dumps({"state": req.state, "verifier": req.verifier}))
    p.chmod(0o600)
    print("Open this URL in a browser as the account owner (consent is the owner's decision):")
    print(req.url)


def cmd_auth_finish(a, c):
    from .http import UrllibTransport
    acct = next((x for x in c.accounts if x.label == a.label), None)
    if not acct:
        raise SystemExit(f"Unknown account label {a.label!r}")
    store = TokenStore()
    pending = json.loads((store.dir / f"{a.label}.pending").read_text())
    a.code, a.state = parse_redirect(a.code, a.state)
    if not a.code or a.state != pending["state"]:
        raise SystemExit("State mismatch; aborting.")
    sf = store.dir / f"{acct.provider}.client_secret"
    creds = ClientCredentials(c.oauth_client_ids.get(acct.provider, ""), sf.read_text().strip() if sf.exists() else "")
    store.save(a.label, exchange_code(acct.provider, creds, REDIRECT, a.code, pending["verifier"], UrllibTransport()))
    (store.dir / f"{a.label}.pending").unlink()
    print(f"Token stored for {a.label}. Set enabled = true in config to use it.")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="sebastian")
    p.add_argument("--config", help="config path (default ~/.config/sebastian/config.toml)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("accounts").set_defaults(fn=cmd_accounts)
    d = sub.add_parser("demo", help="print a digest from fixture data (no network)")
    d.add_argument("kind", choices=["morning", "evening"]); d.set_defaults(fn=cmd_demo)
    g = sub.add_parser("digest", help="fetch live events and print a digest; --send also delivers it")
    g.add_argument("kind", choices=["morning", "evening"]); g.add_argument("--send", action="store_true"); g.set_defaults(fn=cmd_digest)
    r = sub.add_parser("run", help="scheduler loop: digests and reminders")
    r.add_argument("--dry-run", action="store_true", help="use the stub notifier")
    r.add_argument("--interval", type=float, default=60); r.add_argument("--max-ticks", type=int); r.set_defaults(fn=cmd_run)
    u = sub.add_parser("auth-url", help="print the consent URL for an account (does not open or contact anything)")
    u.add_argument("label"); u.set_defaults(fn=cmd_auth_url)
    f = sub.add_parser("auth-finish", help="exchange the code from the redirect for a token (live call)")
    f.add_argument("label"); f.add_argument("code", help="the code, or the whole http://127.0.0.1:8765/callback?... URL"); f.add_argument("state", nargs="?")
    f.set_defaults(fn=cmd_auth_finish)
    b = sub.add_parser("bot", help="Telegram bot for the owner chat (needs SEBASTIAN_ALLOW_LIVE=1, SEBASTIAN_OWNER_CHAT_ID)")
    b.set_defaults(fn=cmd_bot)
    h = sub.add_parser("chat", help="try the assistant in the terminal with a fixture calendar (no network)")
    h.set_defaults(fn=cmd_chat)
    i = sub.add_parser("import-plan", help="propose plan items from a JSON file as calendar events (preview + evet)")
    i.add_argument("file"); i.add_argument("--send", action="store_true", help="also push the preview to the owner chat (live)")
    i.add_argument("--ttl-minutes", type=int, default=1440); i.set_defaults(fn=cmd_import_plan)
    la = sub.add_parser("list-add", help="propose items for a named list (preview + evet), e.g. list-add Alinacaklar 'Uclu priz' Kahve --send")
    la.add_argument("list"); la.add_argument("items", nargs="+"); la.add_argument("--send", action="store_true", help="also push the preview to the owner chat (live)")
    la.add_argument("--ttl-minutes", type=int, default=1440); la.set_defaults(fn=cmd_list_add)
    a = p.parse_args(argv)
    try:
        c = _load(a)
    except FileNotFoundError:
        raise SystemExit("Config not found. Copy config.example.toml to ~/.config/sebastian/config.toml")
    a.fn(a, c)


if __name__ == "__main__":
    main()
