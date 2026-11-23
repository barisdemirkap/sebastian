"""ADR 0023 step 1: task writes from statements. Removing and bulk done/defer go through a preview and a typed 'evet', and always name the
resolved titles; one clearly resolved task may be done/deferred at once but its title is echoed; completion never proposes deleting."""
from test_assistant import make, say, NOW, OWNER


def _tasks(st, *titles):
    for t in titles:
        st.add_task(OWNER, t, NOW, None)


def _open(st):
    return [r["title"] for r in st.open_tasks(OWNER)]


def test_single_done_is_immediate_and_names_the_title():
    a, hub, st, _ = make()
    _tasks(st, "Çamaşır yıka", "Ütü yap")
    out = say(a, "camasir bitti")
    assert "Çamaşır yıka" in out and "SİLMEMİ" not in out and "silebilirim" not in out
    assert _open(st) == ["Ütü yap"]


def test_single_defer_is_immediate_and_names_the_title():
    a, hub, st, _ = make()
    _tasks(st, "Çamaşır yıka", "Ütü yap")
    out = say(a, "camasir yarina kalsin")
    assert "Çamaşır yıka" in out and "gününe aldım" in out


def test_delete_a_task_is_previewed_then_confirmed():
    a, hub, st, _ = make()
    _tasks(st, "Çamaşır yıka", "Ütü yap")
    out = say(a, "utu gorevini kaldir")
    assert "Ütü yap" in out and "(evet / hayır)" in out and len(_open(st)) == 2
    assert "Kaldırdım: Ütü yap" in say(a, "evet") and _open(st) == ["Çamaşır yıka"]


def test_declining_a_task_removal_changes_nothing():
    a, hub, st, _ = make()
    _tasks(st, "Çamaşır yıka", "Ütü yap")
    say(a, "utu gorevini kaldir")
    say(a, "hayır")
    assert len(_open(st)) == 2


def test_bulk_done_lists_the_titles_and_waits():
    a, hub, st, _ = make()
    _tasks(st, "Çamaşır yıka", "Ütü yap", "Pencere sil")
    out = say(a, "hepsi bitti")
    assert all(t in out for t in ("Çamaşır yıka", "Ütü yap", "Pencere sil")) and "(evet / hayır)" in out and len(_open(st)) == 3
    say(a, "evet")
    assert _open(st) == []


def test_bulk_defer_lists_the_titles_and_waits():
    a, hub, st, _ = make()
    _tasks(st, "Çamaşır yıka", "Ütü yap")
    out = say(a, "hepsi yarina kalsin")
    assert "Çamaşır yıka" in out and "Ütü yap" in out and "(evet / hayır)" in out
    assert all(r["due"] is None for r in st.open_tasks(OWNER))
    say(a, "evet")
    assert all(r["due"] for r in st.open_tasks(OWNER))


def test_a_name_that_fits_two_tasks_is_previewed_not_closed_silently():
    a, hub, st, _ = make()
    _tasks(st, "Çamaşır yıka", "Çamaşır ütüle")
    out = say(a, "camasir bitti")
    assert "Çamaşır yıka" in out and "Çamaşır ütüle" in out and len(_open(st)) == 2


def test_confirming_skips_a_task_closed_in_the_meantime():
    a, hub, st, _ = make()
    _tasks(st, "Çamaşır yıka", "Ütü yap")
    say(a, "utu gorevini kaldir")
    st.close_task(st.open_tasks(OWNER)[1]["id"], "done", NOW)
    out = say(a, "evet")
    assert "artık açık değil" in out
