"""ADR 0016: `sebastian list-add` makes ONE pending add preview, sends it to the owner chat with a 24 h limit and logs it."""
from datetime import datetime, timedelta
import json
import pytest
from sebastian import cli
from sebastian import nl as nlmod
from sebastian.assistant import Assistant
from sebastian.providers import FixtureProvider
from sebastian.store import Store
from test_assistant import IST

OWNER = 4242


def cfg_file(tmp_path, live=False):
    c = tmp_path / "c.toml"
    acct = '[[accounts]]\nprovider="google"\naddress="a@b.c"\nlabel="p"\nenabled=true\n' if live else ""
    c.write_text(f'timezone = "Europe/Istanbul"\ndb_path = "{tmp_path / "a.db"}"\n{acct}')
    return c


def test_list_add_stores_one_pending_add_and_confirm_adds_the_items(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SEBASTIAN_OWNER_CHAT_ID", str(OWNER))
    cli.main(["--config", str(cfg_file(tmp_path)), "list-add", "Alınacaklar", "Üçlü priz", "Kahve"])
    out = capsys.readouterr().out
    assert "Alınacaklar listesine şunları ekleyeyim mi?\n- Üçlü priz\n- Kahve\n(evet / hayır)" in out
    st = Store(tmp_path / "a.db")
    row = st.open_pending(OWNER)
    assert row["intent"] == "list_add" and json.loads(row["args_json"]) == {"items": ["Üçlü priz", "Kahve"], "list": "alinacaklar"}
    assert datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"]) == timedelta(hours=24)
    assert st.list_items(OWNER, "alinacaklar") == []                      # nothing is on the list before the owner says yes
    now = datetime.fromisoformat(row["created_at"]) + timedelta(hours=5)
    a = Assistant(FixtureProvider([]), st, nlmod.NLService("off"), IST, OWNER, lambda: now)
    assert "Alınacaklar listesine ekledim: Üçlü priz, Kahve." in a.handle(OWNER, "evet")[0]
    assert [r["text"] for r in st.list_items(OWNER, "alinacaklar")] == ["Üçlü priz", "Kahve"]


def test_send_pushes_the_preview_with_buttons_and_logs_it(tmp_path, monkeypatch):
    monkeypatch.setenv("SEBASTIAN_OWNER_CHAT_ID", str(OWNER))
    monkeypatch.setenv("SEBASTIAN_ALLOW_LIVE", "1")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "telegram.token").write_text("FAKE")
    monkeypatch.setattr(cli, "_secrets_dir", lambda: tmp_path / "secrets")
    sent = []

    class FakeApi:
        def __init__(self, token):
            assert token == "FAKE"

        def send(self, chat, text, reply_markup=None):
            sent.append((chat, text, reply_markup))
            return 77
    monkeypatch.setattr("sebastian.telegram.TelegramApi", FakeApi)
    cli.main(["--config", str(cfg_file(tmp_path, live=True)), "list-add", "alışveriş listesi", "Kahve, Süt", "--send"])
    assert len(sent) == 1 and sent[0][0] == OWNER and "Kahve" in sent[0][1]
    assert [b["text"] for b in sent[0][2]["inline_keyboard"][0]] == ["Kaydet", "İptal"]
    st = Store(tmp_path / "a.db")
    assert st.get_kb(OWNER)[0] == 77 and any(m["direction"] == "out" and "Süt" in m["text"] for m in st.messages(OWNER))
    assert len([1 for r in st.c.execute("SELECT * FROM pending_actions")]) == 1


def test_list_add_needs_owner_known_list_and_live_flag(tmp_path, monkeypatch):
    c = cfg_file(tmp_path, live=True)
    monkeypatch.delenv("SEBASTIAN_OWNER_CHAT_ID", raising=False)
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", str(c), "list-add", "alinacaklar", "x"])
    assert "SEBASTIAN_OWNER_CHAT_ID" in str(e.value)
    monkeypatch.setenv("SEBASTIAN_OWNER_CHAT_ID", str(OWNER))
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", str(c), "list-add", "nope", "x"])
    assert "Unknown list" in str(e.value)
    monkeypatch.delenv("SEBASTIAN_ALLOW_LIVE", raising=False)
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", str(c), "list-add", "alinacaklar", "x", "--send"])
    assert "disabled" in str(e.value)
