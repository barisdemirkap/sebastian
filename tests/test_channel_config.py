"""[channel] config and the channel factory (no network: tokens are fake files, the Slack socket is not opened)."""
import pytest
from sebastian import config as cfg
from sebastian.channels import ConsoleChannel
from sebastian.channels.factory import make_channel
from sebastian.telegram import TelegramApi


def test_default_is_telegram_and_unknown_kinds_are_rejected():
    assert cfg.parse({}).channel.kind == "telegram"
    with pytest.raises(cfg.ConfigError):
        cfg.parse({"channel": {"kind": "fax"}})
    with pytest.raises(cfg.ConfigError):
        cfg.parse({"channel": {"kind": "slack"}})                      # needs slack_user


def test_factory_builds_each_kind(tmp_path):
    (tmp_path / "telegram.token").write_text("123:abc\n")
    assert isinstance(make_channel(cfg.parse({}), tmp_path, 1), TelegramApi)
    assert isinstance(make_channel(cfg.parse({"channel": {"kind": "console"}}), tmp_path, 1), ConsoleChannel)


def test_missing_token_files_stop_with_a_clear_message(tmp_path):
    with pytest.raises(SystemExit, match="Telegram token file not found"):
        make_channel(cfg.parse({}), tmp_path, 1)
    with pytest.raises(SystemExit, match="Slack token file not found"):
        make_channel(cfg.parse({"channel": {"kind": "slack", "slack_user": "U1"}}), tmp_path, 1)
