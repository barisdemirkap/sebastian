from __future__ import annotations
import tomllib
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo
from .models import Account
from .plugins import Plugins, PluginConfigError

CONFIG_DIR = Path.home() / ".config" / "sebastian"
DEFAULT_PATH = CONFIG_DIR / "config.toml"
PROVIDERS = ("google", "outlook")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool = False
    chat_id: str = ""          # the owner's chat only
    token_file: str = ""       # path under the secrets dir holding the bot token


@dataclass(frozen=True)
class ChannelConfig:
    kind: str = "telegram"                       # telegram | slack | console
    slack_user: str = ""                         # your Slack member id (U...), the only sender accepted
    slack_bot_token_file: str = "slack-bot.token"
    slack_app_token_file: str = "slack-app.token"


CHANNEL_KINDS = ("telegram", "slack", "console")


@dataclass(frozen=True)
class Config:
    timezone: str
    morning: time
    evening: time
    accounts: tuple[Account, ...]
    reminder_lead_minutes: int = 30
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    oauth_client_ids: dict = field(default_factory=dict)  # provider -> client_id (secret stays in secrets dir)
    state_path: Path = CONFIG_DIR / "state.json"
    plan: time = time(22, 0)             # "tomorrow morning plan" message
    allday_reminder: time = time(9, 0)   # reminder time for all-day events
    birthdays: bool = True               # also read Google's Birthdays calendar (read-only)
    db_path: Path = CONFIG_DIR / "sebastian.db"
    heartbeat_path: Path = CONFIG_DIR / "heartbeat"
    channel: ChannelConfig = field(default_factory=ChannelConfig)
    owner_title: str = "Ahmet Bey"       # [persona] owner_title: how the assistant addresses you
    assistant_name: str = "Sebastian"    # [persona] assistant_name
    plugins: Plugins = field(default_factory=Plugins)   # [plugins]: name = false switches a feature off (ADR 0005)


def _t(s: str) -> time:
    try:
        h, m = s.split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError) as e:
        raise ConfigError(f"invalid time {s!r}, expected HH:MM") from e


def _channel(raw: dict) -> ChannelConfig:
    kind = str(raw.get("kind", "telegram")).lower()
    if kind not in CHANNEL_KINDS:
        raise ConfigError(f"unknown channel {kind!r}, expected one of {', '.join(CHANNEL_KINDS)}")
    if kind == "slack" and not raw.get("slack_user"):
        raise ConfigError("channel.slack_user (your Slack member id) is required for the slack channel")
    return ChannelConfig(kind, str(raw.get("slack_user", "")), str(raw.get("slack_bot_token_file", "slack-bot.token")),
                         str(raw.get("slack_app_token_file", "slack-app.token")))


def parse(raw: dict) -> Config:
    tz = raw.get("timezone", "UTC")
    try:
        ZoneInfo(tz)
    except Exception as e:
        raise ConfigError(f"unknown timezone {tz!r}") from e
    accounts, labels = [], set()
    for a in raw.get("accounts", []):
        for k in ("provider", "address", "label"):
            if k not in a:
                raise ConfigError(f"account missing {k!r}")
        if a["provider"] not in PROVIDERS:
            raise ConfigError(f"unknown provider {a['provider']!r}")
        if a["label"] in labels:
            raise ConfigError(f"duplicate account label {a['label']!r}")
        labels.add(a["label"])
        accounts.append(Account(a["provider"], a["address"], a["label"], tuple(a.get("scopes", [])), bool(a.get("enabled", False)),
                               bool(a.get("mail_enabled", False))))
    tg = raw.get("telegram", {})
    rem = raw.get("reminders", {})
    lead = int(rem.get("lead_minutes", 30))
    if lead < 1:
        raise ConfigError("reminders.lead_minutes must be >= 1")
    try:
        plugins = Plugins.from_raw(raw.get("plugins"))
    except PluginConfigError as e:
        raise ConfigError(str(e)) from e
    return Config(tz, _t(raw.get("morning", "08:30")), _t(raw.get("evening", "20:00")), tuple(accounts), lead,
                  TelegramConfig(bool(tg.get("enabled", False)), str(tg.get("chat_id", "")), tg.get("token_file", "")),
                  {k: v.get("client_id", "") for k, v in raw.get("oauth", {}).items()},
                  Path(raw.get("state_path", CONFIG_DIR / "state.json")),
                  _t(raw.get("plan", "22:00")), _t(rem.get("allday_at", "09:00")), bool(raw.get("birthdays", True)),
                  Path(raw.get("db_path", CONFIG_DIR / "sebastian.db")), Path(raw.get("heartbeat_path", CONFIG_DIR / "heartbeat")),
                  _channel(raw.get("channel", {})),
                  str(raw.get("persona", {}).get("owner_title", "Ahmet Bey")), str(raw.get("persona", {}).get("assistant_name", "Sebastian")), plugins)


def load(path: Path = DEFAULT_PATH) -> Config:
    return parse(tomllib.loads(Path(path).read_text()))
