"""ADR 0008: English inbound understanding. Dates, times, add-event wording, calendar questions, shopping-list wording
and the confirm flow, all offline. Today is Wednesday 18 November 2026 (the `make` helper's clock, 10:00).
The Turkish guards at the end pin that Turkish text is untouched."""
import sys
from datetime import date, time

import pytest

from test_assistant import NOW, ev, make, say
from sebastian import english, i18n, rules
from sebastian import lists as listmod
from sebastian.timeparse import find_date, find_duration, find_reminder, find_time, mentions_date, mentions_time, names_today_weekday

TODAY = date(2026, 11, 18)      # Wednesday


@pytest.fixture(autouse=True)
def _english_replies():
    i18n.configure(language="en")
    i18n.pin("sir")
    yield


# ---- fixtures: text -> expected reading (the style of the Turkish tables in test_timeparse.py) ----
DATES = [
    ("tomorrow", date(2026, 11, 19)), ("Tomorrow at 3pm", date(2026, 11, 19)), ("today", TODAY), ("tonight", TODAY),
    ("day after tomorrow", date(2026, 11, 20)), ("in 3 days", date(2026, 11, 21)), ("in 2 weeks", date(2026, 12, 2)),
    ("on Friday", date(2026, 11, 20)), ("friday", date(2026, 11, 20)), ("this Friday", date(2026, 11, 20)), ("wednesday", date(2026, 11, 25)),
    ("this Wednesday", TODAY), ("next Friday", date(2026, 11, 27)), ("next week", date(2026, 11, 25)), ("next week on Monday", date(2026, 11, 23)),
    ("on 5 March", date(2027, 3, 5)), ("5th of March", date(2027, 3, 5)), ("March 5", date(2027, 3, 5)), ("March 5th", date(2027, 3, 5)),
    ("5 Mar", date(2027, 3, 5)), ("on 20 November", date(2026, 11, 20)), ("1 December 2028", date(2028, 12, 1)), ("2027-01-09", date(2027, 1, 9)),
    ("Dec 24", date(2026, 12, 24)), ("on Mon", date(2026, 11, 23)),
]


@pytest.mark.parametrize("text,expected", DATES)
def test_english_dates(text, expected):
    assert find_date(text, TODAY)[0] == expected


def test_dates_are_never_invented():
    for text in ["dentist appointment", "may I ask", "I will march home", "30 February", "at 5 pm", "see you at the sun", "5 people"]:
        assert find_date(text, TODAY) is None, text


TIMES = [
    ("3pm", time(15, 0)), ("at 3 PM", time(15, 0)), ("3:30pm", time(15, 30)), ("at 3.30 pm", time(15, 30)), ("10:30 pm", time(22, 30)),
    ("12am", time(0, 0)), ("12 pm", time(12, 0)), ("at 9am", time(9, 0)), ("noon", time(12, 0)), ("at noon", time(12, 0)),
    ("at 15:30", time(15, 30)), ("next Friday 10:30", time(10, 30)), ("at 9", time(9, 0)), ("at 7 in the evening", time(19, 0)),
    ("tomorrow evening at 7", time(19, 0)), ("at 2 at night", time(2, 0)), ("3 o'clock", time(3, 0)), ("at 4 o'clock in the afternoon", time(16, 0)),
]


@pytest.mark.parametrize("text,expected", TIMES)
def test_english_times(text, expected):
    assert find_time(text)[0] == expected


def test_times_are_never_invented():
    for text in ["tomorrow", "dentist", "in the evening", "at the dentist", "5 March", "at home", "for 2 hours", "13pm"]:
        assert find_time(text) is None, text


def test_durations_and_reminders():
    assert find_duration("for 45 minutes", [])[0] == 45 and find_duration("lunch for 2 hours", [])[0] == 120
    assert find_duration("for half an hour", [])[0] == 30 and find_duration("1.5 hours", [])[0] == 90
    assert find_duration("in 2 hours", []) is None and find_duration("30 minutes before", []) is None
    assert find_reminder("remind me 30 minutes before")[0] == 30 and find_reminder("an hour before")[0] == 60
    assert find_reminder("1 day before")[0] == 1440


def test_mentions_guards_accept_english_so_a_model_value_is_not_rejected():
    assert mentions_date("add dentist tomorrow") and mentions_date("on Friday") and mentions_date("next week")
    assert mentions_time("at noon") and mentions_time("tomorrow at nine") and not mentions_date("dentist")


def test_a_bare_weekday_that_is_today_is_asked_about():
    assert names_today_weekday("add dentist on Wednesday", TODAY) and not names_today_weekday("add dentist this Wednesday", TODAY)
    assert not names_today_weekday("add dentist on Friday", TODAY)


@pytest.mark.parametrize("text,at,expected", [
    ("call mom in 2 hours", (10, 0), "call mom today at 12:00"), ("in 45 minutes dentist", (10, 10), "today at 10:55 dentist"),
    ("meeting in half an hour", (10, 0), "meeting today at 10:30"), ("call in an hour", (23, 30), "call tomorrow at 00:30"),
    ("nothing relative here", (10, 0), "nothing relative here")])
def test_relative_start_becomes_a_clock_time(text, at, expected):
    from datetime import datetime
    assert english.resolve_relative(text, NOW.replace(hour=at[0], minute=at[1])) == expected


# ---- add-event wording -> intent ----
def add(text):
    p = rules.parse(text, TODAY)
    assert p["intent"] == "add_event", p
    return p


@pytest.mark.parametrize("text,title,day,start", [
    ("add dentist tomorrow at 3pm", "Dentist", "2026-11-19", "15:00"),
    ("Add a meeting with Sam next Friday 10:30", "Meeting with Sam", "2026-11-27", "10:30"),
    ("add event team sync on Friday at 9:30", "Team sync", "2026-11-20", "09:30"),
    ("schedule lunch with Anna on 5 March at noon", "Lunch with Anna", "2027-03-05", "12:00"),
    ("book a call with Dr. Kaya on March 5th at 7 in the evening", "Call with Dr. Kaya", "2027-03-05", "19:00"),
    ("please add a new meeting called budget review tomorrow at 10am", "Meeting called budget review", "2026-11-19", "10:00"),
    ("remind me to pay rent tomorrow at 9am", "Pay rent", "2026-11-19", "09:00"),
    ("put dentist on my calendar for 12 December at 2pm", "Dentist", "2026-12-12", "14:00"),
    ("create event yoga on Monday 7am", "Yoga", "2026-11-23", "07:00"),
    ("Could you add physio next Friday at 4pm please", "Physio", "2026-11-27", "16:00"),
])
def test_add_event_wordings(text, title, day, start):
    p = add(text)
    assert (p["title"], p["date"], p["time"]) == (title, day, start)


def test_ranges_durations_reminders_and_recurrence():
    p = add("schedule lunch with Anna on 5 March from 12pm to 1:30pm")
    assert (p["time"], p["end_time"]) == ("12:00", "13:30")
    p = add("add gym 10-11am tomorrow")
    assert (p["time"], p["end_time"], p["title"]) == ("10:00", "11:00", "Gym")
    p = add("book physio Friday at 9:30 for 45 minutes")
    assert p["duration_min"] == 45 and p["end_time"] is None
    p = add("add dentist on 12 December at 2pm, remind me 1 hour before")
    assert p["reminder_min"] == 60 and p["title"] == "Dentist"
    p = add("add yoga every Monday at 7am")
    assert p["recurrence"] == "weekly" and p["date"] == "2026-11-23"
    p = add("add stand-up daily at 9am")
    assert p["recurrence"] == "daily" and p["title"] == "Stand-up"
    p = add("add Anna's birthday on 3 March")
    assert (p["all_day"], p["recurrence"], p["tag"], p["title"], p["date"]) == (True, "yearly", "birthday", "Anna's birthday", "2027-03-03")
    p = add("add conference on 20 November all day")
    assert p["all_day"] and p["title"] == "Conference"


def test_missing_pieces_stay_missing():
    p = add("add dentist")
    assert (p["title"], p["date"], p["time"]) == ("Dentist", None, None)
    p = add("add tomorrow at 3pm")
    assert p["title"] is None


QUERIES = [
    ("what's on today", "today"), ("What is on today?", "today"), ("what's on tomorrow", "tomorrow"), ("what do I have tomorrow", "tomorrow"),
    ("what's on this week", "week"), ("what do I have this week?", "week"), ("show me my schedule", "today"), ("my agenda for today", "today"),
    ("what's on my calendar next week", "upcoming"), ("am I free on Friday", "date"), ("what's on on Friday", "date"),
    ("do I have anything on 5 March", "date"), ("anything planned tomorrow", "tomorrow"), ("what's coming up", "upcoming"),
]


@pytest.mark.parametrize("text,rng", QUERIES)
def test_calendar_questions(text, rng):
    p = rules.parse(text, TODAY)
    assert p["intent"] == "list_events" and p["range"] == rng, p


def test_questions_that_are_not_about_the_calendar():
    for text in ["what time is it", "what's on my list", "what do I have to buy", "show me my tasks", "what is the capital of France"]:
        assert rules.parse(text, TODAY)["intent"] != "list_events", text


def test_birthday_question_and_greeting():
    assert rules.parse("what birthdays are coming up", TODAY)["intent"] == "birthdays"
    assert rules.parse("hello", TODAY)["intent"] == "chitchat" and rules.parse("thanks!", TODAY)["intent"] == "chitchat"


# ---- shopping and named lists ----
LISTS = [
    ("add milk to my shopping list", {"kind": "add", "list": "alinacaklar", "items": ["Milk"]}),
    ("Add milk and eggs to the shopping list", {"kind": "add", "list": "alinacaklar", "items": ["Milk", "Eggs"]}),
    ("add milk, eggs, 2 loaves of bread to the list please", {"kind": "add", "list": "alinacaklar", "items": ["Milk", "Eggs", "2 loaves of bread"]}),
    ("put ice cream on my grocery list", {"kind": "add", "list": "alinacaklar", "items": ["Ice cream"]}),
    ("add to shopping list: coffee, tea", {"kind": "add", "list": "alinacaklar", "items": ["Coffee", "Tea"]}),
    ("we're out of olive oil", {"kind": "add", "list": "alinacaklar", "items": ["Olive oil"]}),
    ("what's on my list", {"kind": "show", "list": "alinacaklar"}), ("what's on the shopping list?", {"kind": "show", "list": "alinacaklar"}),
    ("show my shopping list", {"kind": "show", "list": "alinacaklar"}), ("shopping list", {"kind": "show", "list": "alinacaklar"}),
    ("groceries", {"kind": "show", "list": "alinacaklar"}),
    ("remove milk from the shopping list", {"kind": "remove", "list": "alinacaklar", "match": "Milk"}),
    ("take eggs off my list", None),
    ("clear my shopping list", {"kind": "clear", "list": "alinacaklar"}),
    ("I bought milk", {"kind": "check", "match": "Milk"}), ("got the eggs", None), ("bought everything", {"kind": "check_all"}),
]


@pytest.mark.parametrize("text,expected", LISTS)
def test_list_wordings(text, expected):
    got = listmod.parse(text)
    if expected is None:                       # phrasings that are deliberately not read (documented in docs/LANGUAGES.md)
        assert got is None or got["kind"] in ("check", "remove")
    else:
        assert got == expected


def test_a_dated_list_wish_is_a_calendar_matter():
    assert listmod.parse("add milk to the shopping list tomorrow") is None
    assert listmod.parse("add dentist tomorrow at 3pm") is None


def test_list_items_keep_english_capitalisation():
    assert listmod.parse("add ice cream to my list")["items"] == ["Ice cream"]       # the Turkish capitaliser would write 'İce'


# ---- through the assistant: preview + confirm is unchanged ----
def test_add_event_goes_through_preview_and_confirm():
    a, hub, st, clock = make()
    r = say(a, "add dentist tomorrow at 3pm")
    assert "Dentist" in r and "Thursday 19 November 2026 15:00-15:30" in r and "(yes / no)" in r
    assert not hub.events(NOW, NOW.replace(day=25)), "nothing is written before the confirmation"
    assert "Added: Dentist" in say(a, "yes")
    assert [e.title for e in hub.events(NOW, NOW.replace(day=25))] == ["Dentist"]


def test_cancel_drops_the_english_preview():
    a, hub, st, clock = make()
    say(a, "add Meeting with Sam next Friday 10:30")
    assert say(a, "no thanks").startswith("Very well")
    assert not hub.events(NOW, NOW.replace(month=12, day=31))
    say(a, "add dentist tomorrow at 3pm")
    assert say(a, "cancel").startswith("Very well") and not hub.events(NOW, NOW.replace(month=12, day=31))


def test_missing_day_and_time_are_asked_then_read_in_english():
    a, hub, st, clock = make()
    assert say(a, "add dentist").startswith("Which day?")
    assert "At what time?" in say(a, "tomorrow")
    r = say(a, "3pm")
    assert "Thursday 19 November 2026 15:00-15:30" in r
    a, *_ = make()
    say(a, "add dentist")
    say(a, "Friday")
    assert "Friday 20 November 2026 all day" in say(a, "all day")


def test_a_bare_hour_asks_which_half_of_the_day_and_an_english_answer_settles_it():
    a, *_ = make()
    assert "Did you mean 03:00?" in say(a, "add standup tomorrow at 3")
    assert "15:00-15:30" in say(a, "afternoon")
    a, *_ = make()
    say(a, "add standup tomorrow at 3")
    assert "03:00-03:30" in say(a, "morning")


def test_relative_time_is_resolved_in_the_preview():
    a, *_ = make()
    assert "Today Wednesday 18 November 2026 12:00-12:30" in say(a, "add call mom in 2 hours")


def test_today_weekday_is_asked_not_guessed():
    a, *_ = make()
    r = say(a, "add dentist on Wednesday at 3pm")
    assert "1)" in r and "2)" in r


def test_english_calendar_questions_through_the_assistant():
    a, *_ = make([ev("Dentist", 18, 15, eid="d1"), ev("Planning", 19, 9, eid="p1")])
    assert "15:00-16:00 Dentist" in say(a, "what's on today") and "Planning" not in say(a, "what's on today")
    assert "Planning" in say(a, "what's on tomorrow")
    assert "Planning" in say(a, "what's on this week") and "Dentist" in say(a, "what do I have this week")
    assert "Nothing on the calendar" in say(make()[0], "what's on tomorrow")


def test_lists_through_the_assistant():
    a, *_ = make()
    assert "Added to the Shopping list: Milk, Eggs" in say(a, "add milk and eggs to my shopping list")
    r = say(a, "what's on my list")
    assert "1. ☐ Milk" in r and "2. ☐ Eggs" in r
    assert "Ticked off as bought: Milk" in say(a, "I bought milk")
    r = say(a, "remove eggs from the shopping list")
    assert "Eggs" in r and "(yes / no)" in r            # a removal keeps its own preview
    assert "Removed from the list: Eggs" in say(a, "yes")
    assert "Eggs" not in say(a, "show shopping list")


def test_plain_help_is_recognised_in_both_reply_languages():
    a, *_ = make()
    assert "At your service, sir" in say(a, "help") and "At your service, sir" in say(a, "Help me!") and "/commands" in say(a, "commands")
    i18n.configure()
    i18n.pin("efendim")
    assert say(a, "help") == say(a, "/yardim")            # Turkish replies, same help text as the command


def test_help_keeps_an_open_preview():
    a, hub, st, clock = make()
    say(a, "add dentist tomorrow at 3pm")
    say(a, "help")
    assert "Added: Dentist" in say(a, "yes")


def test_custom_title_is_used_in_english_replies():
    a, *_ = make()
    i18n.configure("Ms. Doe", "Sebastian", "en")
    i18n.pin(None)
    i18n.set_rotation(0)
    assert "Ms. Doe" in say(a, "add dentist tomorrow at 3pm") + say(a, "yes")


# ---- Turkish stays as it was ----
TURKISH = ["yarın 15:00 diş hekimi ekle", "cuma öğleden sonra 4-5 gibi toplantı ekle", "bugün ne var", "bu hafta ne var", "süt alınacaklar listesine ekle",
           "alışveriş listesi", "yarın akşam 7'de Ayşe ile buluşma", "3 Mart'ta Ali'nin doğum günü", "haftaya cuma 10:30 toplantı", "bir saat önce hatırlat",
           "doktor randevusu 25 Kasım saat 9", "evet", "hayır", "iptal", "tamam kaydet", "çöp çıkar", "perşembe"]


@pytest.mark.parametrize("text", TURKISH)
def test_english_readers_do_not_claim_turkish_text(text):
    assert english.parse(text, TODAY) is None and english.parse_list(text) is None
    assert english.find_date(text, TODAY) is None or find_date(text, TODAY) is not None


def test_turkish_readers_are_unchanged():
    assert find_date("haftaya cuma", TODAY)[0] == date(2026, 11, 27) and find_time("akşam 7'de")[0] == time(19, 0)
    assert find_time("10:30")[0] == time(10, 30) and find_date("3 Mart", TODAY)[0] == date(2027, 3, 3)
    assert rules.parse("yarın 15:00 diş hekimi ekle", TODAY)["title"] == "Diş hekimi"
    assert listmod.parse("süt ve ekmek alışveriş listesine ekle")["items"] == ["Süt", "Ekmek"]


def test_confirm_and_cancel_vocabulary_is_not_read_as_an_english_command():
    sys.path.insert(0, "tests/conformance")
    import phrasebook_tr as pb
    for text in pb.AFFIRM_BASE + pb.NEGATE_BASE:
        assert english.parse(text, TODAY) in (None, {"intent": "chitchat"}) and english.parse_list(text) is None, text
