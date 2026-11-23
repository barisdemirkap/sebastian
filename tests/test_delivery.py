"""ADR 0010 (D): everything the bot sends is in the message log so it can be read back; stale callback acks are quiet;
import-plan keeps its preview for a day."""
import json
import urllib.error
from datetime import datetime, timedelta, time

from sebastian import cli
from sebastian.models import Event
from sebastian.notify import LoggingNotifier, StubNotifier
from sebastian.scheduler import Scheduler, State
from sebastian.store import Store
from test_bot import make, upd, NOW, OWNER, Clock
from test_buttons import cb, data_of, STALE
from test_import_cli import write_cfg, plan


def test_logging_notifier_sends_then_logs():
    st, inner = Store(), StubNotifier()
    n = LoggingNotifier(inner, st, OWNER, lambda: NOW)
    n.send("Günaydın, efendim. Bugün takvimde bir şey yok.")
    assert inner.sent == ["Günaydın, efendim. Bugün takvimde bir şey yok."]
    assert [(r["direction"], r["text"]) for r in st.messages(OWNER)] == [("out", "Günaydın, efendim. Bugün takvimde bir şey yok.")]


def test_failed_send_is_not_logged():
    class Down:
        def send(self, text):
            raise RuntimeError("down")
    st = Store()
    try:
        LoggingNotifier(Down(), st, OWNER, lambda: NOW).send("x")
    except RuntimeError:
        pass
    assert st.messages(OWNER) == []


def test_scheduler_digest_reaches_the_log_and_can_be_read_back(tmp_path):
    bot, api, st, clock = make()
    n = LoggingNotifier(StubNotifier(), st, OWNER, clock)
    clock.now = NOW.replace(hour=8, minute=31)
    ev = Event("Dis", NOW.replace(hour=15), NOW.replace(hour=16), "p", uid="d")
    Scheduler(time(8, 30), time(20, 0), lambda now: [ev], n, State(tmp_path / "s.json")).tick(clock.now)
    clock.now = NOW.replace(hour=9, minute=5)
    bot.process_update(upd(1, "saat 8 deki mesajı oku"))
    assert api.sent[-1][1].startswith("08:31 civarında şunu yazmıştım:\n")
    assert "Dis" in api.sent[-1][1]


def test_recall_last_message_skips_menu_and_recall_replies():
    bot, api, st, clock = make()
    bot.process_update(upd(1, "/yardim"))
    bot.process_update(upd(2, "az önce ne dediydin"))
    assert api.sent[-1][1].startswith("Az önce şunu yazmıştım:\nEmrinizdeyim, efendim. Yapabildiklerim")
    bot.process_update(upd(3, "son mesajı oku"))
    assert "Emrinizdeyim, efendim. Yapabildiklerim" in api.sent[-1][1] and "Az önce şunu" not in api.sent[-1][1].split("\n", 1)[1][:20]


def test_recall_with_nothing_logged_says_so():
    bot, api, st, clock = make()
    bot.process_update(upd(1, "saat 3 teki mesajı oku"))
    assert api.sent[-1][1] == "Saat 03:00 veya 15:00 civarında yazdığım bir mesaj bulamadım."


def test_recall_keeps_an_open_preview():
    bot, api, st, clock = make()
    bot.process_update(upd(1, "yarın saat 15:00 diş hekimi ekle"))
    pid = st.open_pending(OWNER)["id"]
    bot.process_update(upd(2, "az önce ne dediydin"))
    assert st.open_pending(OWNER)["id"] == pid


class StaleApi:
    """Telegram rejects answerCallbackQuery for an old query with HTTP 400."""
    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, k):
        return getattr(self.inner, k)

    def answer_callback(self, *a, **k):
        raise urllib.error.HTTPError("u", 400, "query is too old", {}, None)


def test_stale_callback_ack_is_quiet_and_the_action_result_is_kept(capsys):
    from sebastian.providers import FixtureProvider
    hub = FixtureProvider([])
    bot, api, st, _ = make(hub=hub)
    bot.process_update(upd(1, "yarın saat 15:00 diş hekimi ekle"))
    k, mid = data_of(api)[0], api.markups[-1][0]
    bot.api = StaleApi(api)
    bot.process_update(cb(2, k, mid=mid))
    assert api.sent[-1][1].startswith("Emredersiniz, efendim. Ekledim: Diş hekimi")
    assert "callback ack error" not in capsys.readouterr().err
    assert len(hub.events(NOW, NOW + timedelta(days=5))) == 1


def test_stale_alert_is_sent_as_a_message_when_the_ack_is_rejected():
    bot, api, st, _ = make()
    bot.api = StaleApi(api)
    bot.process_update(cb(5, "k:99:deadbeef"))
    assert api.sent[-1][1] == STALE


def test_import_plan_default_ttl_is_a_day(tmp_path, monkeypatch):
    monkeypatch.setenv("SEBASTIAN_OWNER_CHAT_ID", str(OWNER))
    c = write_cfg(tmp_path)
    cli.main(["--config", str(c), "import-plan", str(plan(tmp_path, [{"date": "2099-01-05", "title": "Berber"}]))])
    row = Store(tmp_path / "a.db").open_pending(OWNER)
    assert datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"]) == timedelta(minutes=1440)
