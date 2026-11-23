"""ADR 0013: owner to-dos stored in Sebastian's own list; finishing one only OFFERS calendar removal."""
from datetime import time, datetime
from sebastian import i18n
from sebastian.digest import morning_digest, evening_digest
from sebastian.scheduler import Scheduler, State
from sebastian.notify import StubNotifier
from sebastian.tasks import parse
from test_assistant import make, say, ev, window, NOW, OWNER, IST


def test_parser_table():
    assert parse("görev ekle çamaşır") == {"kind": "add", "title": "Çamaşır"}
    assert parse("Görev ekle: Alexa kurulumu") == {"kind": "add", "title": "Alexa kurulumu"}
    assert parse("vergi dairesine gitmem lazım") == {"kind": "add", "title": "Vergi dairesine git"}
    assert parse("çamaşırı bitirdim") == {"kind": "done", "title": "Çamaşırı", "strong": True}
    assert parse("camasir bitti") == {"kind": "done", "title": "Camasir", "strong": True}
    assert parse("ödevi yaptım")["kind"] == "done"
    assert parse("camasir tamam") == {"kind": "done", "title": "Camasir", "strong": False}
    assert parse("görevlerim") == {"kind": "list"}
    assert parse("vergi görevini kaldır") == {"kind": "remove", "title": "Vergi"}
    for none in ("tamam", "evet", "camasir bitti mi?", "yarın 15:00 toplantı yapmam lazım", "bugün ne var", "camasiri kaldir", "ok tamam"):
        assert parse(none) is None, none


def test_add_list_duplicate_and_done_without_an_event():
    a, hub, st, _ = make()
    assert say(a, "görev ekle çamaşır") == "Emredersiniz, efendim. Görev ekledim: Çamaşır"
    assert say(a, "görev ekle çamaşır") == "Bu görev zaten listenizde: Çamaşır"
    assert say(a, "görevlerim") == "Görevleriniz:\n1. Çamaşır"
    assert say(a, "çamaşırı bitirdim") == "Tamamlandı olarak işaretledim: Çamaşır"
    assert say(a, "/gorevler") == "Açık göreviniz yok."
    assert [r["status"] for r in st.c.execute("SELECT status FROM tasks")] == ["done"]


def test_done_with_a_matching_event_today_closes_it_and_never_offers_a_delete():
    a, hub, st, _ = make([ev("Çamaşır", 18, 15)])
    say(a, "görev ekle çamaşır")
    out = say(a, "çamaşır bitti")
    assert out.startswith("Tamamlandı olarak işaretledim: Çamaşır")
    assert "Takvimde tamamlandı olarak işaretledim (silmedim): Çamaşır" in out and "SİLMEMİ" not in out and "silebilirim" not in out
    assert st.open_pending(OWNER) is None and len(window(hub)) == 1       # the calendar is untouched; the event is closed in Sebastian's store
    assert len(st.done_event_ids(OWNER)) == 1


def test_event_only_completion_closes_the_event_of_today():
    a, hub, st, _ = make([ev("Çamaşır", 18, 9)])
    out = say(a, "camasir bitti")
    assert "silmedim" in out and st.open_pending(OWNER) is None and len(hub._events) == 1


def test_an_event_of_tomorrow_is_not_closed_by_a_completion():
    a, hub, st, _ = make([ev("Çamaşır", 19, 15)])
    assert say(a, "camasir bitti") == i18n.t("task_none") and st.open_pending(OWNER) is None and not st.done_event_ids(OWNER)


def test_an_event_beyond_tomorrow_is_not_offered():
    a, hub, st, _ = make([ev("Çamaşır", 25, 15)])
    assert say(a, "camasir bitti") == i18n.t("task_none") and st.open_pending(OWNER) is None


def test_two_matching_events_close_the_one_that_has_started():
    a, hub, st, _ = make([ev("Çamaşır sabah", 18, 9, eid="a"), ev("Çamaşır akşam", 18, 20, eid="b")])
    out = say(a, "camasir bitti")
    assert "Çamaşır sabah" in out and "SİLMEMİ" not in out and st.open_pending(OWNER) is None and len(hub._events) == 2
    assert st.done_event_ids(OWNER) == {"a"}


def test_weak_tamam_needs_a_match_and_falls_through_otherwise():
    a, _, st, _ = make()
    assert say(a, "camasir tamam") != i18n.t("task_none")
    say(a, "görev ekle çamaşır")
    assert say(a, "camasir tamam") == "Tamamlandı olarak işaretledim: Çamaşır"


def test_removing_a_task_by_name_and_by_plain_kaldir():
    a, hub, st, _ = make()
    say(a, "görev ekle vergi dairesine git")
    out = say(a, "vergi görevini kaldır")
    assert "Vergi dairesine git" in out and "(evet / hayır)" in out and len(st.open_tasks(OWNER)) == 1      # a removal is previewed, never silent
    assert "Kaldırdım: Vergi dairesine git" in say(a, "evet")
    say(a, "görev ekle berber")
    assert "Berber" in say(a, "berberi kaldır") and len(st.open_tasks(OWNER)) == 1
    assert "Kaldırdım: Berber" in say(a, "evet")
    assert say(a, "görevlerim") == "Açık göreviniz yok."
    assert say(a, "vergi görevini kaldır") == "Bu isimde açık bir görev bulamadım: Vergi"


def test_event_wins_over_task_for_plain_kaldir():
    a, hub, st, _ = make([ev("Berber", 19, 15)])
    say(a, "görev ekle berber")
    assert "SİLMEMİ" in say(a, "berberi kaldır") and len(st.open_tasks(OWNER)) == 1


def test_digests_list_open_tasks():
    assert morning_digest([], NOW, tasks=["Çamaşır", "Berber"]).endswith("Görevler:\n- Çamaşır\n- Berber")
    assert evening_digest([], NOW, tasks=["Çamaşır"]).endswith("Görevler:\n- Çamaşır")
    assert "Görevler" not in morning_digest([], NOW)


def test_scheduler_digest_carries_the_tasks_and_survives_a_broken_task_source(tmp_path):
    n = StubNotifier()
    s = Scheduler(time(8, 30), time(20, 0), lambda now: [], n, State(tmp_path / "s.json"), tasks=lambda: ["Çamaşır"])
    s.tick(NOW.replace(hour=8, minute=31))
    assert n.sent[0].endswith("Görevler:\n- Çamaşır")

    def boom():
        raise RuntimeError("db")
    n2 = StubNotifier()
    Scheduler(time(8, 30), time(20, 0), lambda now: [], n2, State(tmp_path / "t.json"), tasks=boom).tick(NOW.replace(hour=8, minute=31))
    assert n2.sent == ["Günaydın, efendim. Bugün takvimde bir şey yok."]


def test_tasks_survive_a_restart(tmp_path):
    from sebastian.store import Store
    s = Store(tmp_path / "a.db")
    s.add_task(OWNER, "Çamaşır", NOW)
    assert [r["title"] for r in Store(tmp_path / "a.db").open_tasks(OWNER)] == ["Çamaşır"]


def test_help_mentions_tasks_and_read_back_within_the_line_budget():
    a, *_ = make()
    h = say(a, "/yardim")
    assert "görev ekle çamaşır" in h and len(h.splitlines()) <= 9
    assert "az önce ne dediydin" in say(a, "/rehber")
    assert "/gorevler" in say(a, "/komutlar")


def test_multi_clause_done_and_chore_sentence_does_not_fall_to_open_question():
    """'aile hekimi yapildi cöp aksam cikacak': the first clause closes, the second is a chore (here already listed)."""
    a, hub, st, _ = make([ev("Aile hekimi", 18, 15)])
    say(a, "görev ekle Çöpü akşam çıkar")
    out = say(a, "aile hekimi yapildi cöp aksam cikacak")
    assert "Tam anlayamadım" not in out
    assert "Aile hekimi" in out and "Bu görev zaten listenizde: Çöpü akşam çıkar" in out
    assert len(st.open_tasks(OWNER)) == 1                      # no duplicate


def test_multi_clause_adds_a_new_chore_and_closes_a_task():
    a, hub, st, _ = make()
    say(a, "görev ekle aile hekimi")
    out = say(a, "aile hekimi yapildi cöp aksam cikacak")
    assert "Tamamlandı olarak işaretledim: Aile hekimi" in out and "Görev ekledim: Cöp aksam cikacak" in out
    assert [r["title"] for r in st.open_tasks(OWNER)] == ["Cöp aksam cikacak"]


def test_split_clauses_and_parser():
    from sebastian.tasks import split_clauses
    assert split_clauses("aile hekimi yapildi cöp aksam cikacak") == ["aile hekimi yapildi", "cöp aksam cikacak"]
    assert split_clauses("camasir bitti ve bulasik yikanacak") == ["camasir bitti", "bulasik yikanacak"]
    assert parse("aile hekimi yapildi")["kind"] == "done"
    assert parse("çöp akşam çıkacak")["kind"] == "add" and parse("hava güzel olacak") is None


def test_unsure_makes_a_best_guess_instead_of_not_understood():
    a, hub, st, _ = make()
    out = say(a, "asdf qwer")
    assert out.startswith("Tam anlayamadım")                  # gibberish still has no reading
    assert a._best_guess("diş hekimi randevusu", a.clock()) is not None


# -- 2026-11-19 failures: whole-list statements, hourly nag, "bir tane daha ekle"
def _tasks(st, *titles):
    for t in titles:
        st.add_task(OWNER, t, NOW)


def test_bulk_done_keeps_named_tasks_and_closes_the_others():
    a, hub, st, _ = make()
    _tasks(st, "Çöpü çıkar", "Acme metin kontrolü", "Hue ışıkları", "Araç muayenesi randevusu")
    out = say(a, "çöp  acme hala duruyor digerlerini done yapbilirsin bu saate kadar gecmis olanlar")
    assert "Hue ışıkları" in out and "Araç muayenesi randevusu" in out and "(evet / hayır)" in out
    assert len(st.open_tasks(OWNER)) == 4                                 # previewed: nothing closed before the yes
    assert "Tamamlandı olarak işaretledim" in say(a, "evet")
    assert [r["title"] for r in st.open_tasks(OWNER)] == ["Çöpü çıkar", "Acme metin kontrolü"]
    assert len(list(st.c.execute("SELECT 1 FROM tasks"))) == 4            # no new task named with the sentence


def test_schedule_tasks_after_six_is_applied_not_asked():
    a, hub, st, _ = make()
    _tasks(st, "Çöpü çıkar", "Acme metin kontrolü", "Hue ışıkları", "Araç muayenesi randevusu")
    out = say(a, "çöp ve acme aksam 6dan sonra hue'lar da aksam 6dan sonra arac muayenesi de 6dan sonra")
    assert "18:00'den sonra" in out and out.count("18:00") == 4 and "?" not in out
    assert len(st.open_tasks(OWNER)) == 4


def test_nag_sets_up_and_is_not_the_answer_to_a_time_question():
    a, hub, st, _ = make()
    _tasks(st, "A işi", "B işi")
    say(a, "bir tane daha ekle amcamin trafik cezasini odeyecegiz")
    say(a, "yarın diş hekimi ekle")
    assert st.get_conversation(OWNER)["awaiting"] == "time"
    out = say(a, "saat aksam 6dan sonra saat başı hatirlatsin 4 yapilmamis işi")
    assert "18:00" in out and "saat başı" in out
    assert st.get_conversation(OWNER)["awaiting"] == "time" and st.open_pending(OWNER) is None
    assert say(a, "saat 6dan sonra saat basi hepsini hatirlat").count("18:00") == 1


def test_nag_fires_hourly_from_the_start_until_tasks_close():
    from sebastian.bot import task_nag_state
    a, hub, st, clock = make()
    _tasks(st, "A işi", "B işi")
    say(a, "saat 6dan sonra saat basi hepsini hatirlat")
    sent = StubNotifier()
    sch = Scheduler(time(8), time(21), lambda n: [], sent, State(None), nag=lambda n: task_nag_state(st, OWNER, n))
    at = lambda h, m=0: datetime(2026, 11, 18, h, m, tzinfo=IST)
    assert sch.tick(at(17, 59)) == [] and sch.tick(at(18, 0))[0].count("- ") == 2
    assert sch.tick(at(18, 1)) == [] and len(sch.tick(at(19, 0))) == 1
    for r in st.open_tasks(OWNER):
        st.close_task(r["id"], "done", NOW)
    assert sch.tick(at(20, 0)) == []


def test_bir_tane_daha_ekle_with_a_todo_verb_adds_a_task():
    a, hub, st, _ = make()
    assert parse("bir tane daha ekle amcamin trafik cezasini odeyecegiz")["kind"] == "add"
    out = say(a, "bir tane daha ekle amcamin trafik cezasini odeyecegiz")
    assert "Görev ekledim" in out and "Tam anlayamadım" not in out
    assert [r["title"] for r in st.open_tasks(OWNER)] == ["Amcamin trafik cezasini odeyecegiz"]


# -- 2026-11-19: 'after 18:00' MOVES today's events (preview + evet); 'yarin'in listesine' is a task, not a time question
def test_after_six_moves_todays_events_sequentially_after_evet():
    a, hub, st, _ = make([ev("Çöpü çıkar", 18, 12), ev("Acme Türkçe metin kontrolü", 18, 13), ev("Alexa ve Hue kurulumları", 18, 14),
                          ev("Araç muayenesi için randevu al", 18, 15), ev("Akşam yemeği", 18, 18, dur=60), ev("Yarın işi", 19, 12)])
    _tasks(st, "Çöpü çıkar", "Acme metin kontrolü", "Hue ışıkları", "Araç muayenesi randevusu")
    out = say(a, "çöp ve acme aksam 6dan sonra hue'lar da aksam 6dan sonra arac muayenesi de 6dan sonra")
    assert "18:00'den sonra" in out and out.count("18:00'den sonra") == 4 and "?" not in out.replace("mısınız?", "")
    assert "4 etkinliği" in out and "eşleşen" not in out
    p = st.open_pending(OWNER)
    assert p["intent"] == "edit_events"
    starts = [m["start"][11:16] for m in __import__("json").loads(p["args_json"])["moves"]]
    assert starts == ["19:00", "20:00", "21:00", "22:00"]            # 18:00-19:00 is busy; each slot follows the previous
    before = {e.title: e.start.hour for e in window(hub)}
    assert before["Çöpü çıkar"] == 12                                  # nothing written before 'evet'
    assert "4 etkinliği akşama taşıdım" in say(a, "evet")
    after = {e.title: e.start.hour for e in window(hub)}
    assert [after[t] for t in ("Çöpü çıkar", "Acme Türkçe metin kontrolü", "Alexa ve Hue kurulumları", "Araç muayenesi için randevu al")] == [19, 20, 21, 22]
    assert after["Yarın işi"] == 12 and after["Akşam yemeği"] == 18


def test_after_six_reports_names_that_match_nothing():
    a, hub, st, _ = make([ev("Çöpü çıkar", 18, 12)])
    out = say(a, "çöp aksam 6dan sonra yoga da aksam 6dan sonra")
    assert "Çöpü çıkar" in out and "yoga" in out and "eşleşen" in out
    assert st.open_pending(OWNER)["intent"] == "edit_events"


def test_tomorrow_list_adds_a_task_and_never_asks_for_a_time():
    a, hub, st, _ = make()
    out = say(a, "yarin'in listesine duş ve kişisel bakim ekle")
    assert "Saat kaçta" not in out and "aynı durumdayız" not in out
    assert "Yarının listesine görev ekledim: Duş ve kişisel bakim" in out
    rows = list(st.c.execute("SELECT title, due FROM tasks WHERE status='open'"))
    assert [(r["title"], r["due"]) for r in rows] == [("Duş ve kişisel bakim", "2026-11-19")]
    assert st.get_conversation(OWNER) is None and window(hub) == []


def test_split_clauses_keeps_clock_times_and_trailing_time_words_with_their_clause():
    from sebastian.tasks import split_clauses
    assert split_clauses("kurulumlari yaptim çöp aksam cikacak 18.30da amcamin trafik cezalari odenecek aksam 23te") == [
        "kurulumlari yaptim", "çöp aksam cikacak 18.30da", "amcamin trafik cezalari odenecek aksam 23te"]
    assert split_clauses("bugun yapilacak islerim neler")[-1] == "islerim neler"


def test_meta_sentence_is_never_stored_as_a_task_and_is_asked_about():
    a, hub, st, _ = make()
    out = say(a, "çöp hala duruyor gerisini sen hallet bu saate kadar gecmis olanlar")
    assert "Görev ekledim" not in out and "kaydetmedim" in out
    assert st.open_tasks(OWNER) == []
