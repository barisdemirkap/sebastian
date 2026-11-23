"""Fork-and-run: a fresh checkout works offline with no config, `init` writes a safe starter, `doctor` says what is missing."""
import io
import contextlib
import pytest
from sebastian import cli, config as cfg


def run(argv, monkeypatch=None):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(argv)
    return out.getvalue()


def test_init_writes_a_private_offline_starter_that_parses(tmp_path):
    path = tmp_path / "cfg" / "config.toml"
    out = run(["--config", str(path), "init", "--timezone", "Europe/Berlin", "--title", "Dr. Kaya", "--name", "Jeeves"])
    c = cfg.load(path)
    assert (c.timezone, c.owner_title, c.assistant_name, c.channel.kind, c.accounts) == ("Europe/Berlin", "Dr. Kaya", "Jeeves", "console", ())
    assert oct(path.stat().st_mode & 0o777) == "0o600" and "sebastian chat" in out


def test_init_never_overwrites_without_force(tmp_path):
    path = tmp_path / "config.toml"
    run(["--config", str(path), "init"])
    with pytest.raises(SystemExit, match="already exists"):
        run(["--config", str(path), "init"])
    run(["--config", str(path), "init", "--force"])


def test_doctor_reports_missing_pieces(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_secrets_dir", lambda: tmp_path / "secrets")
    monkeypatch.delenv("SEBASTIAN_ALLOW_LIVE", raising=False)
    path = tmp_path / "config.toml"
    path.write_text('timezone = "UTC"\n[channel]\nkind = "telegram"\n')
    out = run(["--config", str(path), "doctor"])
    assert "[ok] config" in out and "[--] telegram token" in out and "[--] owner chat id" in out and "to fix" in out


def test_missing_config_message_points_to_init(tmp_path):
    with pytest.raises(SystemExit, match="sebastian init"):
        run(["--config", str(tmp_path / "nope.toml"), "accounts"])
