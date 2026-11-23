"""Typo repair of the key words (diger / gorev / bugun / plan) and the plan-read recognisers: edit distance 1, no false positives."""
import pytest

from sebastian import planread as pr


@pytest.mark.parametrize("said,fixed", [("doger gorevlerim", "diger gorevlerim"), ("digre gorevlerim", "digre gorevlerim".replace("digre", "diger")),
                                        ("bugin planim", "bugun planim"), ("paln", "plan"), ("goreylerim", "gorevlerim"),
                                        ("döğer görevlerim", "diger gorevlerim")])
def test_typos_are_repaired(said, fixed):
    assert pr.fix(said) == fixed


@pytest.mark.parametrize("word", ["deger", "dugun", "gorsel", "gorusme", "plaj", "flan", "klan", "dogru", "gunes", "gorev", "diger", "bugun"])
def test_real_words_are_left_alone(word):
    assert pr.fix(word) == word


def test_plan_reads_and_non_reads():
    for t in ["guncel bugun planim nasil?", "bugunku plan", "planim ne", "bugunun programi nedir", "paln nasil"]:
        assert pr.is_plan_read(t), t
    for t in ["plaj plani nasil", "bugun plan yap", "yarin planim ne", "bugun 5te plan ekle", "dugun planim nasil", "hafta plani"]:
        assert not pr.is_plan_read(t), t


def test_missing_tasks_question():
    assert pr.is_missing_tasks_question("ayrica doger gorevlerim neden bugunku planda yok?")
    assert pr.is_missing_tasks_question("gorevlerim niye planda gorunmuyor")
    assert not pr.is_missing_tasks_question("neden bugun toplanti var")
    assert not pr.is_missing_tasks_question("diger gorevlerim")
